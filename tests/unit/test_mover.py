"""Unit tests for :mod:`winspace.core.mover`.

We split the suite into:

* **Happy path** — full 9-step flow against real files + real junctions.
  These tests need Windows because they rely on ``mklink /J``.
* **Failure injection** — each guard / cleanup branch is exercised by
  monkeypatching the underlying primitive (fs.copytree, fs.rename, …)
  to raise, then asserting that the user's data ends up in a sane
  place (either fully restored at the source, or fully copied to the
  destination — never half-deleted).
* **Undo** — both happy path and the broken / missing / mismatch branches.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from winspace.core import junction as junction_module
from winspace.core import mover as mover_module
from winspace.core.errors import (
    FsError,
    InsufficientSpaceError,
    JunctionError,
    ManifestError,
    MoveAbortedError,
    SafetyViolation,
    VerificationError,
)
from winspace.core.fs import RealFileSystem
from winspace.core.junction import is_junction
from winspace.core.manifest import EntryStatus, load
from winspace.core.mover import (
    DeleteResult,
    MoveResult,
    UndoResult,
    execute_delete,
    execute_move,
    execute_undo,
)

windows_only = pytest.mark.skipif(os.name != "nt", reason="mover happy paths use real mklink /J")


# --- helpers / fixtures ------------------------------------------------------


def _make_tree(root: Path, layout: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, value in layout.items():
        target = root / name
        if isinstance(value, int):
            target.write_text("x" * value)
        elif isinstance(value, str):
            target.write_text(value)
        elif isinstance(value, dict):
            _make_tree(target, value)


@pytest.fixture
def drives(tmp_path: Path) -> tuple[Path, Path]:
    """Two pseudo-drives. drive_c hosts the source; drive_d hosts the move."""
    drive_c = tmp_path / "drive_c"
    drive_d = tmp_path / "drive_d"
    drive_c.mkdir()
    drive_d.mkdir()
    return drive_c, drive_d


@pytest.fixture
def source_tree(drives: tuple[Path, Path]) -> Path:
    drive_c, _ = drives
    src = drive_c / "Users" / "me" / "node_modules"
    _make_tree(src, {"package.json": "{}", "lodash": {"index.js": "module.exports"}})
    return src


@pytest.fixture
def manifest_path(tmp_path: Path) -> Path:
    return tmp_path / "manifest.json"


# --- precheck failures (no junction needed) ---------------------------------


def test_refuses_to_move_never_path(drives: tuple[Path, Path], manifest_path: Path) -> None:
    _, drive_d = drives
    with pytest.raises(SafetyViolation, match="windows-system-dir"):
        execute_move(Path("C:\\Windows\\System32"), drive_d, manifest_path=manifest_path)


def test_refuses_missing_source(drives: tuple[Path, Path], manifest_path: Path) -> None:
    drive_c, drive_d = drives
    with pytest.raises(SafetyViolation, match="does not exist"):
        execute_move(drive_c / "nope", drive_d, manifest_path=manifest_path)


def test_refuses_source_that_is_a_file(drives: tuple[Path, Path], manifest_path: Path) -> None:
    drive_c, drive_d = drives
    f = drive_c / "loose.txt"
    f.write_text("hi")
    with pytest.raises(SafetyViolation, match="not a directory"):
        execute_move(f, drive_d, manifest_path=manifest_path)


def test_refuses_source_that_is_reparse_point(
    drives: tuple[Path, Path],
    manifest_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drive_c, drive_d = drives
    src = drive_c / "looks-like-junction"
    src.mkdir()
    monkeypatch.setattr(RealFileSystem, "is_reparse_point", lambda self, p: p == src)
    with pytest.raises(SafetyViolation, match="reparse point"):
        execute_move(src, drive_d, manifest_path=manifest_path)


# --- insufficient-space precheck --------------------------------------------


def test_refuses_when_insufficient_space(
    source_tree: Path,
    drives: tuple[Path, Path],
    manifest_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, drive_d = drives
    monkeypatch.setattr(RealFileSystem, "get_free_space", lambda self, p: 0)
    with pytest.raises(InsufficientSpaceError):
        execute_move(source_tree, drive_d, manifest_path=manifest_path)
    # Source survives untouched.
    assert source_tree.is_dir()
    assert (source_tree / "package.json").read_text() == "{}"


# --- dry-run does not mutate ------------------------------------------------


def test_dry_run_does_not_touch_anything(
    source_tree: Path,
    drives: tuple[Path, Path],
    manifest_path: Path,
) -> None:
    _, drive_d = drives
    result = execute_move(source_tree, drive_d, manifest_path=manifest_path, dry_run=True)
    assert result.dry_run is True
    assert result.size_bytes > 0
    assert result.entry_id == "dry-run"
    # Nothing should have been written:
    assert source_tree.is_dir()
    assert not (drive_d / "winspace" / "node_modules").exists()
    assert not manifest_path.exists()


# --- happy path: real-mklink end-to-end -------------------------------------


@windows_only
@pytest.mark.windows
def test_happy_path_full_cycle(
    source_tree: Path,
    drives: tuple[Path, Path],
    manifest_path: Path,
) -> None:
    _, drive_d = drives
    result = execute_move(source_tree, drive_d, manifest_path=manifest_path)

    assert isinstance(result, MoveResult)
    assert result.dry_run is False
    assert result.cleanup_pending is False
    assert result.file_count >= 2

    # Source path is now a junction onto the new location.
    assert is_junction(source_tree)
    assert (source_tree / "package.json").read_text() == "{}"

    # The new location holds the real data.
    new_dir = drive_d / "winspace" / "node_modules"
    assert new_dir.is_dir()
    assert (new_dir / "package.json").read_text() == "{}"

    # Manifest contains an active entry referencing both paths.
    m = load(manifest_path)
    [entry] = m.entries
    assert entry.status == EntryStatus.ACTIVE
    assert Path(entry.original_path).samefile(source_tree)


@windows_only
@pytest.mark.windows
def test_allocates_unique_destination_when_name_collides(
    source_tree: Path,
    drives: tuple[Path, Path],
    manifest_path: Path,
) -> None:
    _, drive_d = drives
    # Pre-create the target so the first slot is taken.
    (drive_d / "winspace" / "node_modules").mkdir(parents=True)
    result = execute_move(source_tree, drive_d, manifest_path=manifest_path)
    assert result.dst.name == "node_modules-2"


@windows_only
@pytest.mark.windows
def test_undo_restores_source_and_cleans_destination(
    source_tree: Path,
    drives: tuple[Path, Path],
    manifest_path: Path,
) -> None:
    _, drive_d = drives
    move = execute_move(source_tree, drive_d, manifest_path=manifest_path)

    undo = execute_undo(move.entry_id, manifest_path=manifest_path)
    assert isinstance(undo, UndoResult)
    assert undo.size_bytes == move.size_bytes
    assert undo.restored_path.samefile(source_tree)

    # Source is back to a real directory; destination is gone.
    assert not is_junction(source_tree)
    assert (source_tree / "package.json").read_text() == "{}"
    assert not move.dst.exists()

    # Manifest entry is rolled_back.
    m = load(manifest_path)
    [entry] = m.entries
    assert entry.status == EntryStatus.ROLLED_BACK


# --- failure injection: copy fails (step 4) ---------------------------------


def test_copy_failure_leaves_source_intact(
    source_tree: Path,
    drives: tuple[Path, Path],
    manifest_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, drive_d = drives

    def boom(self: RealFileSystem, src: Path, dst: Path) -> None:
        # Pretend robocopy left a partial dst behind.
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "partial").write_text("oops")
        raise FsError("simulated copy failure")

    monkeypatch.setattr(RealFileSystem, "copytree", boom)
    with pytest.raises(MoveAbortedError, match="copy failed"):
        execute_move(source_tree, drive_d, manifest_path=manifest_path)

    # Source intact, partial destination cleaned up, no manifest entry.
    assert source_tree.is_dir()
    assert (source_tree / "package.json").read_text() == "{}"
    assert not (drive_d / "winspace" / "node_modules").exists()
    assert not manifest_path.exists()


# --- failure injection: verify mismatch (step 5) ----------------------------


def test_verify_mismatch_cleans_destination_and_keeps_source(
    source_tree: Path,
    drives: tuple[Path, Path],
    manifest_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force the post-copy fingerprint to differ by mutating dst between
    copytree and fingerprint.
    """
    _, drive_d = drives
    real_copytree = RealFileSystem.copytree

    def tamper(self: RealFileSystem, src: Path, dst: Path) -> None:
        real_copytree(self, src, dst)
        # Drop a regular file so the destination's fingerprint diverges.
        for child in dst.rglob("*"):
            if child.is_file():
                child.unlink()
                break

    monkeypatch.setattr(RealFileSystem, "copytree", tamper)
    with pytest.raises(VerificationError):
        execute_move(source_tree, drive_d, manifest_path=manifest_path)

    assert source_tree.is_dir()
    assert (source_tree / "package.json").read_text() == "{}"
    # Destination cleaned up.
    assert not (drive_d / "winspace" / "node_modules").exists()


# --- failure injection: rename fails (step 6) -------------------------------


def test_rename_failure_aborts_and_cleans_destination(
    source_tree: Path,
    drives: tuple[Path, Path],
    manifest_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, drive_d = drives

    def boom(self: RealFileSystem, old: Path, new: Path) -> None:
        raise OSError("rename denied")

    monkeypatch.setattr(RealFileSystem, "rename", boom)
    with pytest.raises(MoveAbortedError, match="rename source aside"):
        execute_move(source_tree, drive_d, manifest_path=manifest_path)

    assert source_tree.is_dir()
    assert not (drive_d / "winspace" / "node_modules").exists()


# --- failure injection: junction creation fails (step 7) --------------------


@windows_only
@pytest.mark.windows
def test_junction_failure_restores_source_and_cleans_destination(
    source_tree: Path,
    drives: tuple[Path, Path],
    manifest_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, drive_d = drives

    def boom(*_a: object, **_kw: object) -> None:
        raise JunctionError("simulated mklink failure")

    monkeypatch.setattr(mover_module, "create_junction", boom)
    with pytest.raises(MoveAbortedError, match="junction creation failed"):
        execute_move(source_tree, drive_d, manifest_path=manifest_path)

    # Source must be restored to its original location, not orphaned
    # as `.winspace-old-<ts>`.
    assert source_tree.is_dir()
    assert (source_tree / "package.json").read_text() == "{}"
    assert not is_junction(source_tree)
    # Destination cleaned up.
    assert not (drive_d / "winspace" / "node_modules").exists()


# --- failure injection: manifest write fails (step 8) -----------------------


@windows_only
@pytest.mark.windows
def test_manifest_failure_rolls_back_everything(
    source_tree: Path,
    drives: tuple[Path, Path],
    manifest_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, drive_d = drives

    def boom(*_a: object, **_kw: object) -> None:
        raise ManifestError("disk full")

    monkeypatch.setattr(mover_module, "append_entry", boom)
    with pytest.raises(MoveAbortedError, match="manifest write failed"):
        execute_move(source_tree, drive_d, manifest_path=manifest_path)

    # Source restored, junction gone, destination cleaned up.
    assert source_tree.is_dir()
    assert (source_tree / "package.json").read_text() == "{}"
    assert not is_junction(source_tree)
    assert not (drive_d / "winspace" / "node_modules").exists()


# --- step 9 cleanup failure marks cleanup_pending ---------------------------


@windows_only
@pytest.mark.windows
def test_cleanup_failure_sets_cleanup_pending(
    source_tree: Path,
    drives: tuple[Path, Path],
    manifest_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If rmtree of the renamed-aside source fails, the move still
    succeeds but cleanup_pending is set on the manifest entry.
    """
    _, drive_d = drives

    real_rmtree = RealFileSystem.rmtree
    call_state = {"saw_rename_old": False}

    def flaky_rmtree(self: RealFileSystem, path: Path) -> None:
        if ".winspace-old-" in path.name:
            call_state["saw_rename_old"] = True
            raise OSError("file in use")
        real_rmtree(self, path)

    monkeypatch.setattr(RealFileSystem, "rmtree", flaky_rmtree)
    result = execute_move(source_tree, drive_d, manifest_path=manifest_path)

    assert call_state["saw_rename_old"], "expected to attempt rmtree on .winspace-old"
    assert result.cleanup_pending is True

    m = load(manifest_path)
    [entry] = m.entries
    assert entry.cleanup_pending is True
    assert entry.status == EntryStatus.ACTIVE


# --- _allocate_destination exhaustion ---------------------------------------


def test_allocate_destination_exhaustion(
    drives: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """If every candidate name is taken, raise rather than loop forever."""
    _, drive_d = drives
    monkeypatch.setattr(RealFileSystem, "exists", lambda self, p: True)
    with pytest.raises(FsError, match="could not allocate"):
        mover_module._allocate_destination(drive_d, "node_modules", RealFileSystem())


# --- undo failure paths -----------------------------------------------------


def test_undo_missing_entry_raises(manifest_path: Path) -> None:
    with pytest.raises(ManifestError, match="no manifest entry"):
        execute_undo("nope", manifest_path=manifest_path)


@windows_only
@pytest.mark.windows
def test_undo_rejects_non_active_entry(
    source_tree: Path,
    drives: tuple[Path, Path],
    manifest_path: Path,
) -> None:
    _, drive_d = drives
    move = execute_move(source_tree, drive_d, manifest_path=manifest_path)
    execute_undo(move.entry_id, manifest_path=manifest_path)
    with pytest.raises(ManifestError, match="status is"):
        execute_undo(move.entry_id, manifest_path=manifest_path)


@windows_only
@pytest.mark.windows
def test_undo_rejects_original_that_is_not_junction(
    source_tree: Path,
    drives: tuple[Path, Path],
    manifest_path: Path,
) -> None:
    _, drive_d = drives
    move = execute_move(source_tree, drive_d, manifest_path=manifest_path)
    # Mutilate the manifest so original_path doesn't point at the junction.
    decoy = source_tree.parent / "decoy"
    decoy.mkdir()
    m = load(manifest_path)
    m.entries[0].original_path = str(decoy)
    from winspace.core.manifest import save

    save(m, manifest_path)
    with pytest.raises(JunctionError, match="not a junction"):
        execute_undo(move.entry_id, manifest_path=manifest_path)


@windows_only
@pytest.mark.windows
def test_undo_copy_back_failure_restores_junction(
    source_tree: Path,
    drives: tuple[Path, Path],
    manifest_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, drive_d = drives
    move = execute_move(source_tree, drive_d, manifest_path=manifest_path)

    def boom(self: RealFileSystem, src: Path, dst: Path) -> None:
        raise FsError("simulated copy-back failure")

    monkeypatch.setattr(RealFileSystem, "copytree", boom)
    with pytest.raises(MoveAbortedError):
        execute_undo(move.entry_id, manifest_path=manifest_path)

    # Junction should have been restored so data is still reachable.
    assert is_junction(source_tree)


@windows_only
@pytest.mark.windows
def test_undo_verify_mismatch_marks_entry_broken(
    source_tree: Path,
    drives: tuple[Path, Path],
    manifest_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, drive_d = drives
    move = execute_move(source_tree, drive_d, manifest_path=manifest_path)

    # Tamper with the copy-back so its fingerprint differs from manifest.
    real_copytree = RealFileSystem.copytree

    def tamper(self: RealFileSystem, src: Path, dst: Path) -> None:
        real_copytree(self, src, dst)
        # Find the first regular file and remove it so fingerprint diverges.
        for child in dst.rglob("*"):
            if child.is_file():
                child.unlink()
                break

    monkeypatch.setattr(RealFileSystem, "copytree", tamper)
    with pytest.raises(VerificationError):
        execute_undo(move.entry_id, manifest_path=manifest_path)

    m = load(manifest_path)
    [entry] = m.entries
    assert entry.status == EntryStatus.BROKEN


# --- execute_delete -------------------------------------------------------


def test_delete_refuses_never_path(manifest_path: Path) -> None:
    with pytest.raises(SafetyViolation):
        execute_delete(Path("C:\\Windows\\System32"), manifest_path=manifest_path)


def test_delete_refuses_missing_source(drives: tuple[Path, Path], manifest_path: Path) -> None:
    drive_c, _ = drives
    with pytest.raises(SafetyViolation, match="does not exist"):
        execute_delete(drive_c / "ghost", manifest_path=manifest_path)


def test_delete_refuses_source_that_is_a_file(
    drives: tuple[Path, Path], manifest_path: Path
) -> None:
    drive_c, _ = drives
    f = drive_c / "loose.txt"
    f.write_text("hi")
    with pytest.raises(SafetyViolation, match="not a directory"):
        execute_delete(f, manifest_path=manifest_path)


def test_delete_refuses_source_that_is_reparse_point(
    drives: tuple[Path, Path],
    manifest_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drive_c, _ = drives
    src = drive_c / "looks-junction"
    src.mkdir()
    monkeypatch.setattr(RealFileSystem, "is_reparse_point", lambda self, p: p == src)
    with pytest.raises(SafetyViolation, match="reparse point"):
        execute_delete(src, manifest_path=manifest_path)


def test_delete_dry_run_does_not_mutate(source_tree: Path, manifest_path: Path) -> None:
    result = execute_delete(source_tree, manifest_path=manifest_path, dry_run=True)
    assert isinstance(result, DeleteResult)
    assert result.dry_run is True
    assert result.size_bytes > 0
    assert result.entry_id == "dry-run"
    # Source untouched, no manifest written.
    assert source_tree.is_dir()
    assert (source_tree / "package.json").read_text() == "{}"
    assert not manifest_path.exists()


def test_delete_happy_path_records_entry_and_removes_source(
    source_tree: Path, manifest_path: Path
) -> None:
    pre = source_tree
    result = execute_delete(pre, manifest_path=manifest_path)

    assert isinstance(result, DeleteResult)
    assert result.dry_run is False
    assert result.size_bytes > 0
    assert result.file_count > 0

    # Source gone.
    assert not pre.exists()

    # Manifest has one DELETED entry.
    from winspace.core.manifest import load

    m = load(manifest_path)
    [entry] = m.entries
    assert entry.status == EntryStatus.DELETED
    assert entry.new_path == ""
    assert entry.size_bytes == result.size_bytes
    assert entry.original_path == str(pre)


def test_delete_manifest_write_failure_leaves_source_intact(
    source_tree: Path,
    manifest_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the manifest can't be written we MUST abort BEFORE rmtree —
    losing data with no forensic record is the worst possible outcome.
    """

    def boom(*_a: object, **_kw: object) -> None:
        raise ManifestError("disk full")

    monkeypatch.setattr(mover_module, "append_entry", boom)

    with pytest.raises(MoveAbortedError, match="manifest write failed before delete"):
        execute_delete(source_tree, manifest_path=manifest_path)

    # Source intact, no rmtree happened.
    assert source_tree.is_dir()
    assert (source_tree / "package.json").read_text() == "{}"


def test_delete_rmtree_failure_marks_entry_broken(
    source_tree: Path,
    manifest_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If rmtree fails partway, the manifest entry is marked BROKEN so
    the doctor command can flag the user.
    """

    def boom_rmtree(self: RealFileSystem, path: Path) -> None:
        raise OSError("file in use")

    monkeypatch.setattr(RealFileSystem, "rmtree", boom_rmtree)

    with pytest.raises(MoveAbortedError, match="delete failed"):
        execute_delete(source_tree, manifest_path=manifest_path)

    from winspace.core.manifest import load

    m = load(manifest_path)
    [entry] = m.entries
    assert entry.status == EntryStatus.BROKEN


# --- silence reference-only imports for ruff --------------------------------

_ = junction_module
