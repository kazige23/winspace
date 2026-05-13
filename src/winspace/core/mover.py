"""Reverse-protection move workflow.

The Mover implements spec §3 and plan T11's locked 9-step flow:

1. Precheck — path safety, source-not-junction, dst writable
2. Fingerprint source — file count, bytes, tree hash
3. Free-space check -- dst must hold src x 1.1
4. Copy source to dst location (under ``<dst_drive>:\\winspace\\<basename>``)
5. Fingerprint dst, compare with source. Mismatch ⇒ clean up new copy.
6. Rename source aside as ``<src>.winspace-old-<ts>``
7. Create junction at the original ``src`` path pointing to dst
8. Write manifest entry (status=active)
9. Delete the renamed-aside source; failure here just marks
   ``cleanup_pending`` on the manifest entry

The key invariant: **at every step boundary, the user's data remains
accessible at the original path**. Failures earlier than step 7 leave
the source untouched; failures at step 7 onwards already have the
data at the destination *and* either the renamed-aside source
restored, or the junction successfully placed.

The undo flow reverses the move with the same care: delete junction,
copy back, verify fingerprint matches the saved one, then remove the
destination copy. A verify mismatch leaves both copies in place and
marks the manifest entry ``broken`` so the doctor command can flag it.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from winspace.core.errors import (
    FsError,
    InsufficientSpaceError,
    JunctionError,
    ManifestError,
    MoveAbortedError,
    SafetyViolation,
    VerificationError,
)
from winspace.core.fs import FileSystem, RealFileSystem
from winspace.core.junction import create_junction, delete_junction, is_junction
from winspace.core.manifest import (
    EntryStatus,
    ManifestEntry,
    append_entry,
    load,
    update_status,
)
from winspace.core.safety import is_never
from winspace.core.verify import Fingerprint, compare, fingerprint

# A move is rejected if the target lacks at least 110% of the source size,
# leaving 10% headroom for FS metadata, sector quantisation, and the
# robocopy temp buffers.
_SPACE_HEADROOM_RATIO = 1.10


@dataclass(frozen=True)
class MoveResult:
    """Summary of a successful move (or a dry-run)."""

    entry_id: str
    src: Path
    dst: Path
    size_bytes: int
    file_count: int
    tree_hash: str
    dry_run: bool
    cleanup_pending: bool = False


@dataclass(frozen=True)
class UndoResult:
    """Summary of a successful undo."""

    entry_id: str
    restored_path: Path
    removed_path: Path
    size_bytes: int


# --- execute_move ----------------------------------------------------------


def execute_move(
    src: Path,
    dst_drive: Path,
    *,
    fs: FileSystem | None = None,
    manifest_path: Path | None = None,
    dry_run: bool = False,
) -> MoveResult:
    """Run the 9-step move flow. See module docstring for invariants.

    Parameters
    ----------
    src
        The directory to relocate. Must be a non-junction directory not
        on the NEVER list.
    dst_drive
        The drive (or root directory in tests) that should host the
        relocated tree. Files end up at ``<dst_drive>/winspace/<basename>``.
    fs
        Filesystem implementation. Defaults to :class:`RealFileSystem`.
    manifest_path
        Where to store the manifest. ``None`` uses the default location
        (``%APPDATA%\\winspace\\manifest.json``).
    dry_run
        If True, the function performs only the precheck steps and
        returns immediately without modifying any state.
    """
    fs = fs or RealFileSystem()

    # --- step 1: precheck --------------------------------------------------
    _precheck_source(src, fs)
    _precheck_destination_drive(dst_drive, fs)

    # --- step 2: source fingerprint ---------------------------------------
    src_fp = fingerprint(src, fs=fs)

    # --- step 3: free-space check -----------------------------------------
    required = int(src_fp.total_bytes * _SPACE_HEADROOM_RATIO)
    available = fs.get_free_space(dst_drive)
    if available < required:
        raise InsufficientSpaceError(
            f"{dst_drive} has {available} bytes free; need >= {required} "
            f"(source {src_fp.total_bytes} x {_SPACE_HEADROOM_RATIO:.2f} headroom)"
        )

    new_path = _allocate_destination(dst_drive, src.name, fs)

    if dry_run:
        # Return a synthetic result describing what *would* happen.
        return MoveResult(
            entry_id="dry-run",
            src=src,
            dst=new_path,
            size_bytes=src_fp.total_bytes,
            file_count=src_fp.file_count,
            tree_hash=src_fp.tree_hash,
            dry_run=True,
        )

    # --- step 4: copy -----------------------------------------------------
    try:
        fs.copytree(src, new_path)
    except Exception as e:
        _best_effort_rmtree(new_path, fs)
        raise MoveAbortedError(f"copy failed: {e}") from e

    # --- step 5: verify ---------------------------------------------------
    dst_fp = fingerprint(new_path, fs=fs)
    diff = compare(src_fp, dst_fp)
    if not diff.same:
        _best_effort_rmtree(new_path, fs)
        raise VerificationError(
            f"fingerprint mismatch after copy: "
            f"count {src_fp.file_count}/{dst_fp.file_count}, "
            f"bytes {src_fp.total_bytes}/{dst_fp.total_bytes}, "
            f"hash {'OK' if diff.tree_hash_match else 'MISMATCH'}"
        )

    # --- step 6: rename source aside --------------------------------------
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    old_src_path = src.with_name(src.name + f".winspace-old-{ts}")
    try:
        fs.rename(src, old_src_path)
    except Exception as e:
        _best_effort_rmtree(new_path, fs)
        raise MoveAbortedError(f"could not rename source aside: {e}") from e

    # --- step 7: create junction ------------------------------------------
    try:
        create_junction(src, new_path, fs=fs)
    except JunctionError as e:
        # Restore source to its original location, then drop the copy.
        _best_effort_rename(old_src_path, src, fs)
        _best_effort_rmtree(new_path, fs)
        raise MoveAbortedError(f"junction creation failed: {e}") from e

    # --- step 8: write manifest entry -------------------------------------
    entry = ManifestEntry.new(
        original_path=src,
        new_path=new_path,
        size_bytes=src_fp.total_bytes,
        file_count=src_fp.file_count,
        tree_hash=src_fp.tree_hash,
    )
    try:
        append_entry(entry, manifest_path, fs=fs)
    except ManifestError as e:
        # Best-effort rollback: drop junction, restore source, drop dst.
        _best_effort_delete_junction(src, fs)
        _best_effort_rename(old_src_path, src, fs)
        _best_effort_rmtree(new_path, fs)
        raise MoveAbortedError(f"manifest write failed: {e}") from e

    # --- step 9: delete renamed-aside source ------------------------------
    cleanup_pending = False
    try:
        fs.rmtree(old_src_path)
    except (OSError, FsError):
        cleanup_pending = True
        with contextlib.suppress(ManifestError):
            # Even the bookkeeping update failing leaves the orphan
            # discoverable by the doctor command, so we swallow it.
            update_status(
                entry.id,
                EntryStatus.ACTIVE,
                manifest_path,
                fs=fs,
                cleanup_pending=True,
            )

    return MoveResult(
        entry_id=entry.id,
        src=src,
        dst=new_path,
        size_bytes=src_fp.total_bytes,
        file_count=src_fp.file_count,
        tree_hash=src_fp.tree_hash,
        dry_run=False,
        cleanup_pending=cleanup_pending,
    )


# --- execute_undo ----------------------------------------------------------


def execute_undo(
    entry_id: str,
    *,
    fs: FileSystem | None = None,
    manifest_path: Path | None = None,
) -> UndoResult:
    """Reverse a previous move identified by ``entry_id``.

    1. Locate the manifest entry, refuse if not active
    2. Verify the original path is currently a junction
    3. Delete the junction
    4. Copy data from new_path back to original_path
    5. Verify the restored fingerprint matches the manifest record
       (mismatch ⇒ status=broken, keep both copies for forensics)
    6. Remove new_path
    7. Mark manifest entry rolled_back
    """
    fs = fs or RealFileSystem()

    manifest = load(manifest_path, fs=fs)
    entry = manifest.find_by_id(entry_id)
    if entry is None:
        raise ManifestError(f"no manifest entry with id={entry_id}")
    if entry.status != EntryStatus.ACTIVE:
        raise ManifestError(f"cannot undo entry {entry_id}: status is {entry.status}")

    original = Path(entry.original_path)
    relocated = Path(entry.new_path)

    if not is_junction(original, fs=fs):
        raise JunctionError(f"original path {original} is not a junction; manifest may be stale")

    # Step 3: drop the junction. The relocated copy is the canonical data
    # for now — we restore from it.
    delete_junction(original, fs=fs)

    # Step 4: copy back.
    try:
        fs.copytree(relocated, original)
    except Exception as e:
        # Restore the junction so the data is still reachable.
        _best_effort_create_junction(original, relocated, fs)
        raise MoveAbortedError(f"copy-back failed during undo: {e}") from e

    # Step 5: verify the restored copy matches the manifest fingerprint.
    restored = fingerprint(original, fs=fs)
    expected = Fingerprint(
        file_count=entry.file_count,
        total_bytes=entry.size_bytes,
        tree_hash=entry.tree_hash,
    )
    if not compare(expected, restored).same:
        # Keep both copies; mark entry broken.
        _best_effort_update_status(entry_id, EntryStatus.BROKEN, manifest_path, fs)
        raise VerificationError(
            f"fingerprint mismatch after undo: "
            f"count {expected.file_count}/{restored.file_count}, "
            f"bytes {expected.total_bytes}/{restored.total_bytes}"
        )

    # Step 6: drop the relocated copy. If this fails, the restored copy
    # at the original path is still intact — the leftover is harmless and
    # the doctor command can detect and offer to clean it later.
    with contextlib.suppress(OSError, FsError):
        fs.rmtree(relocated)

    # Step 7: mark rolled back.
    update_status(entry_id, EntryStatus.ROLLED_BACK, manifest_path, fs=fs)

    return UndoResult(
        entry_id=entry_id,
        restored_path=original,
        removed_path=relocated,
        size_bytes=entry.size_bytes,
    )


# --- precheck helpers ------------------------------------------------------


def _precheck_source(src: Path, fs: FileSystem) -> None:
    blocked, rule = is_never(src)
    if blocked:
        raise SafetyViolation(f"refusing to move {src}: rule={rule}")
    if not fs.exists(src):
        raise SafetyViolation(f"source does not exist: {src}")
    if not fs.is_dir(src):
        raise SafetyViolation(f"source is not a directory: {src}")
    if fs.is_reparse_point(src):
        raise SafetyViolation(f"source is already a reparse point (already relocated?): {src}")


def _precheck_destination_drive(dst_drive: Path, fs: FileSystem) -> None:
    # We don't require the drive root to exist (tests pass tmp paths);
    # but it must be createable and end up as a directory.
    fs.mkdir(dst_drive / "winspace", parents=True, exist_ok=True)


def _allocate_destination(dst_drive: Path, basename: str, fs: FileSystem) -> Path:
    """Pick a non-colliding path under ``<dst_drive>/winspace/``.

    First choice is ``<dst_drive>/winspace/<basename>``; if that's taken,
    append ``-2``, ``-3``, … up to ``-999`` before giving up.
    """
    root = dst_drive / "winspace"
    candidate = root / basename
    if not fs.exists(candidate):
        return candidate
    for i in range(2, 1000):
        candidate = root / f"{basename}-{i}"
        if not fs.exists(candidate):
            return candidate
    raise FsError(f"could not allocate a unique destination under {root}")


# --- best-effort cleanup wrappers ------------------------------------------


def _best_effort_rmtree(path: Path, fs: FileSystem) -> None:
    if not fs.exists(path):
        return
    with contextlib.suppress(OSError, FsError):
        fs.rmtree(path)


def _best_effort_rename(old: Path, new: Path, fs: FileSystem) -> None:
    if not fs.exists(old):
        return
    with contextlib.suppress(OSError, FsError):
        fs.rename(old, new)


def _best_effort_delete_junction(path: Path, fs: FileSystem) -> None:
    if not is_junction(path, fs=fs):
        return
    with contextlib.suppress(JunctionError):
        delete_junction(path, fs=fs)


def _best_effort_create_junction(link: Path, target: Path, fs: FileSystem) -> None:
    with contextlib.suppress(JunctionError):
        create_junction(link, target, fs=fs)


def _best_effort_update_status(
    entry_id: str,
    status: EntryStatus,
    manifest_path: Path | None,
    fs: FileSystem,
) -> None:
    with contextlib.suppress(ManifestError):
        update_status(entry_id, status, manifest_path, fs=fs)
