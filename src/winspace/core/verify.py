"""Tree fingerprint for fast post-copy verification.

Hashing every file's content on a multi-GB directory would dominate
the move budget. Instead we compute a much cheaper "tree fingerprint"
based on the *structural* invariants that a robocopy must preserve:

* file count (catches missing or extra files)
* total byte sum (catches truncated copies)
* SHA-256 of the sorted ``(relative-path, size)`` list (catches
  renames, additions, and removals that the first two miss)

Per-file content hashing is intentionally NOT done: robocopy + NTFS
already guard against silent bit-rot, and the cost of full hashing
turns a 1-minute Steam library copy into a 20-minute ordeal.

The fingerprint is location-independent: ``fingerprint(C:\\app)``
and ``fingerprint(D:\\app)`` produce equal results when both trees
hold identical relative layouts. Reparse points are skipped (we do
not follow them) so junctions in either side never contaminate.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

from winspace.core.fs import FileSystem, RealFileSystem


@dataclass(frozen=True)
class Fingerprint:
    """Compact structural fingerprint of a directory tree."""

    file_count: int
    total_bytes: int
    tree_hash: str  # hex SHA-256


@dataclass(frozen=True)
class FingerprintDiff:
    """Per-axis breakdown of two fingerprints' equality."""

    same: bool
    file_count_match: bool
    total_bytes_match: bool
    tree_hash_match: bool


_EMPTY_HASH = hashlib.sha256(b"").hexdigest()


def fingerprint(root: Path, *, fs: FileSystem | None = None) -> Fingerprint:
    """Compute the fingerprint of every file under ``root``.

    Reparse points are not followed. Missing roots yield the empty
    fingerprint. Permission errors on subtrees are swallowed (those
    files simply contribute zero to all three numbers — under-counting
    is better than crashing in the middle of verification).
    """
    fs = fs or RealFileSystem()
    if not fs.exists(root) or fs.is_reparse_point(root):
        return Fingerprint(0, 0, _EMPTY_HASH)

    pairs: list[tuple[str, int]] = []
    total_bytes = 0
    stack: list[Path] = [root]

    while stack:
        current = stack.pop()
        try:
            children = list(fs.iterdir(current))
        except (PermissionError, OSError):
            continue
        for child in children:
            if fs.is_reparse_point(child):
                continue
            if fs.is_dir(child):
                stack.append(child)
                continue
            try:
                size = fs.stat(child).st_size
            except OSError:
                continue
            rel = child.relative_to(root).as_posix()
            pairs.append((rel, size))
            total_bytes += size

    pairs.sort()
    h = hashlib.sha256()
    for rel, size in pairs:
        # NUL is illegal in filenames on every relevant OS, so it makes a
        # safe delimiter between the path and the size in the hashed stream.
        h.update(f"{rel}\x00{size}\n".encode())

    return Fingerprint(
        file_count=len(pairs),
        total_bytes=total_bytes,
        tree_hash=h.hexdigest(),
    )


def compare(a: Fingerprint, b: Fingerprint) -> FingerprintDiff:
    """Diff two fingerprints axis-by-axis. ``same`` is True iff all axes match."""
    file_count_match = a.file_count == b.file_count
    total_bytes_match = a.total_bytes == b.total_bytes
    tree_hash_match = a.tree_hash == b.tree_hash
    return FingerprintDiff(
        same=file_count_match and total_bytes_match and tree_hash_match,
        file_count_match=file_count_match,
        total_bytes_match=total_bytes_match,
        tree_hash_match=tree_hash_match,
    )


def fingerprint_with_timing(
    root: Path, *, fs: FileSystem | None = None
) -> tuple[Fingerprint, float]:
    """:func:`fingerprint` plus wall-clock seconds spent computing it.

    Used by the doctor command and by tests asserting performance budgets.
    """
    start = time.perf_counter()
    fp = fingerprint(root, fs=fs)
    return fp, time.perf_counter() - start
