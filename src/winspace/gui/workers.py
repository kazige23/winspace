"""Background workers for the GUI.

Each worker is a ``QObject`` that runs in a dedicated ``QThread`` so the
main window never freezes during a scan / move / delete. Workers emit
signals on success or failure; the window connects those signals to
slot methods that update the table and status bar.

The actual filesystem work delegates to the existing engine functions
(:mod:`winspace.detectors.base`, :mod:`winspace.core.mover`,
:mod:`winspace.core.manifest`) so behaviour matches the CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6 import QtCore
from PySide6.QtCore import QObject, Signal

from winspace.core.fs import RealFileSystem
from winspace.core.manifest import EntryStatus, ManifestEntry, load
from winspace.core.mover import (
    MoveResult,
    execute_delete,
    execute_move,
    execute_undo,
)
from winspace.core.scanner import directory_size
from winspace.detectors.base import Candidate, RiskLevel, discover_detectors


@dataclass(frozen=True)
class OperationOutcome:
    """Result of a single move/delete attempt."""

    path: Path
    success: bool
    message: str
    freed_bytes: int = 0


# --- scan worker -----------------------------------------------------------


class ScanWorker(QObject):
    """Run every detector, enrich with size, hand back ranked candidates."""

    finished = Signal(list)  # list[tuple[Candidate, int]]
    failed = Signal(str)

    def __init__(self, *, include_risky: bool = False) -> None:
        super().__init__()
        self._include_risky = include_risky

    @QtCore.Slot()
    def run(self) -> None:
        try:
            fs = RealFileSystem()
            never_roots: list[Path] = []
            raw: list[Candidate] = []
            for det in discover_detectors():
                try:
                    raw.extend(det.find(fs))
                except Exception as e:
                    # One bad detector shouldn't fail the whole scan.
                    self.failed.emit(f"detector {det.name} crashed: {e}")
            # First pass: capture NEVER roots so the cascade can apply.
            for c in raw:
                if c.risk == RiskLevel.NEVER:
                    never_roots.append(_resolve(c.path))

            ranked: list[tuple[Candidate, int]] = []
            for c in raw:
                if c.risk == RiskLevel.NEVER:
                    continue
                if c.risk == RiskLevel.RISKY and not self._include_risky:
                    continue
                if _is_under_any(_resolve(c.path), never_roots):
                    continue
                size = c.size_bytes or directory_size(c.path)
                ranked.append((c, size))
            ranked.sort(key=lambda pair: pair[1], reverse=True)
            self.finished.emit(ranked)
        except Exception as e:
            self.failed.emit(str(e))


# --- move / delete workers --------------------------------------------------


class MoveWorker(QObject):
    """Move a batch of paths to the same destination drive."""

    progress = Signal(int, int, Path)  # done, total, current
    finished = Signal(list)  # list[OperationOutcome]

    def __init__(self, paths: list[Path], to_drive: Path) -> None:
        super().__init__()
        self._paths = paths
        self._to_drive = to_drive

    @QtCore.Slot()
    def run(self) -> None:
        outcomes: list[OperationOutcome] = []
        total = len(self._paths)
        for i, path in enumerate(self._paths, start=1):
            self.progress.emit(i, total, path)
            try:
                result = execute_move(path, self._to_drive)
                outcomes.append(
                    OperationOutcome(
                        path=path,
                        success=True,
                        message=f"moved to {result.dst}",
                        freed_bytes=_freed_bytes_from_move(result),
                    )
                )
            except Exception as e:
                outcomes.append(OperationOutcome(path=path, success=False, message=str(e)))
        self.finished.emit(outcomes)


class DeleteWorker(QObject):
    """Delete a batch of paths."""

    progress = Signal(int, int, Path)
    finished = Signal(list)

    def __init__(self, paths: list[Path]) -> None:
        super().__init__()
        self._paths = paths

    @QtCore.Slot()
    def run(self) -> None:
        outcomes: list[OperationOutcome] = []
        total = len(self._paths)
        for i, path in enumerate(self._paths, start=1):
            self.progress.emit(i, total, path)
            try:
                result = execute_delete(path)
                outcomes.append(
                    OperationOutcome(
                        path=path,
                        success=True,
                        message="deleted",
                        freed_bytes=result.size_bytes,
                    )
                )
            except Exception as e:
                outcomes.append(OperationOutcome(path=path, success=False, message=str(e)))
        self.finished.emit(outcomes)


class UndoLastWorker(QObject):
    """Undo the most recent ACTIVE manifest entry."""

    finished = Signal(object)  # UndoResult or None
    failed = Signal(str)

    @QtCore.Slot()
    def run(self) -> None:
        try:
            manifest = load()
            active = [e for e in manifest.entries if e.status == EntryStatus.ACTIVE]
            if not active:
                self.failed.emit("没有可撤销的迁移 / no active moves to undo")
                return
            latest = max(active, key=lambda e: e.timestamp)
            result = execute_undo(latest.id)
            self.finished.emit(result)
        except Exception as e:
            self.failed.emit(str(e))


# --- helpers ----------------------------------------------------------------


def _resolve(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return path


def _is_under_any(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _freed_bytes_from_move(result: MoveResult) -> int:
    """A move doesn't actually free bytes on the source drive when the
    junction is on the same volume; the GUI status bar still reports
    the relocated size so the user can see how much "moved off C:".
    """
    return result.size_bytes


def get_active_manifest_entries() -> list[ManifestEntry]:
    """Convenience for the GUI's "Undo" button enablement."""
    return [e for e in load().entries if e.status == EntryStatus.ACTIVE]


__all__ = (
    "DeleteWorker",
    "MoveWorker",
    "OperationOutcome",
    "ScanWorker",
    "UndoLastWorker",
    "get_active_manifest_entries",
)
