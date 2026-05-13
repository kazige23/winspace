"""Directory size scanner.

Two layers:

* :func:`directory_size` is the workhorse — recursive byte size of a
  directory tree, stopping at reparse points (junctions, symlinks) so
  we never double-count nor follow loops.
* :func:`scan` walks one root's *immediate* children and returns the
  Top-N largest as :class:`ScanEntry` records. NEVER paths are skipped
  before any descent.

Detectors do their own structured walks based on application-specific
heuristics (e.g. node_modules at depth ≤ 4), but they all share
``directory_size`` for the final byte count of each candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from winspace.core.fs import FileSystem, RealFileSystem
from winspace.core.safety import is_never


@dataclass(frozen=True)
class ScanEntry:
    """One immediate child of a scan root, with size and safety annotation."""

    path: Path
    size_bytes: int
    is_reparse_point: bool
    safety_rule: str  # "" if not blocked; rule name otherwise


def directory_size(path: Path, *, fs: FileSystem | None = None) -> int:
    """Total bytes under ``path``, recursive, without following reparse points.

    Iterative DFS using an explicit stack so deep trees don't blow Python's
    recursion limit. Permission errors and individual stat failures on
    leaf files are swallowed (we'd rather under-count than crash mid-scan).
    """
    fs = fs or RealFileSystem()
    if not fs.exists(path) or fs.is_reparse_point(path):
        return 0

    total = 0
    stack: list[Path] = [path]
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
            else:
                try:
                    total += fs.stat(child).st_size
                except OSError:
                    # Individual file unreadable (locked, ACL, etc.) — skip.
                    continue
    return total


def scan(
    root: Path,
    *,
    top_n: int = 30,
    min_size: int = 0,
    fs: FileSystem | None = None,
) -> list[ScanEntry]:
    """Return Top-N immediate sub-directories of ``root`` by size, descending.

    * NEVER paths (matched via :func:`safety.is_never`) are dropped entirely.
    * Reparse points (junctions, symlinks) appear with ``size_bytes=0`` and
      ``is_reparse_point=True`` so callers can surface them in diagnostic UI
      without recursing into them — but they are filtered out of the
      ranked result because they wouldn't pass ``min_size`` anyway.
    * If ``root`` itself is on the NEVER list, returns an empty list.
    """
    fs = fs or RealFileSystem()

    blocked, _ = is_never(root)
    if blocked:
        return []
    if not fs.exists(root) or not fs.is_dir(root):
        return []

    candidates: list[ScanEntry] = []
    try:
        children = list(fs.iterdir(root))
    except (PermissionError, OSError):
        return []

    for child in children:
        if not fs.is_dir(child):
            continue
        child_blocked, rule = is_never(child)
        is_reparse = fs.is_reparse_point(child)
        size = 0 if (child_blocked or is_reparse) else directory_size(child, fs=fs)
        candidates.append(
            ScanEntry(
                path=child,
                size_bytes=size,
                is_reparse_point=is_reparse,
                safety_rule=rule,
            )
        )

    # Drop blocked / reparse / undersized entries from the ranked output.
    ranked = [
        e
        for e in candidates
        if not e.safety_rule and not e.is_reparse_point and e.size_bytes >= min_size
    ]
    ranked.sort(key=lambda e: e.size_bytes, reverse=True)
    return ranked[:top_n]
