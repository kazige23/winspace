"""``QAbstractTableModel`` for the scan-results table.

Columns:

    0 — checkbox (selection state)
    1 — path (full)
    2 — size (human-formatted)
    3 — risk tier
    4 — category
    5 — reason (Chinese)

Each row stores a ``(Candidate, size_bytes, checked)`` tuple. The
``checked`` flag is mutated by user interaction; sizes / sort order
are set once when scan results arrive.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6 import QtCore
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)

from winspace.detectors.base import Candidate, RiskLevel

# Qt method overrides accept either kind of index.
_QtIndex = QModelIndex | QPersistentModelIndex

# Per-column metadata. Order matches the displayed column order.
_HEADERS = ("✓", "路径 / Path", "大小 / Size", "风险 / Risk", "类型 / Category", "说明 / Reason")
_HEADER_COUNT = len(_HEADERS)

# 0-based column indices, exported for use by the main window code.
COL_CHECK = 0
COL_PATH = 1
COL_SIZE = 2
COL_RISK = 3
COL_CATEGORY = 4
COL_REASON = 5


@dataclass
class CandidateRow:
    """One row in the table — mutable so the checkbox state can flip."""

    candidate: Candidate
    size_bytes: int
    checked: bool = False


def _format_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


class CandidateTableModel(QAbstractTableModel):
    """Read-mostly model. The only mutable bit is the per-row check state."""

    selection_changed = QtCore.Signal()

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[CandidateRow] = []

    # --- Qt overrides ---------------------------------------------------

    def rowCount(self, parent: _QtIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: _QtIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else _HEADER_COUNT

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _HEADERS[section]
        return None

    def data(self, index: _QtIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole and col == COL_CHECK:
            return Qt.CheckState.Checked if row.checked else Qt.CheckState.Unchecked

        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_PATH:
                return str(row.candidate.path)
            if col == COL_SIZE:
                return _format_size(row.size_bytes)
            if col == COL_RISK:
                return row.candidate.risk.value
            if col == COL_CATEGORY:
                return row.candidate.category
            if col == COL_REASON:
                return row.candidate.reason_zh

        if role == Qt.ItemDataRole.TextAlignmentRole and col == COL_SIZE:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.ToolTipRole and col == COL_REASON:
            return f"{row.candidate.reason_zh}\n{row.candidate.reason_en}"

        return None

    def flags(self, index: _QtIndex) -> Qt.ItemFlag:
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == COL_CHECK:
            base |= Qt.ItemFlag.ItemIsUserCheckable
        return base

    def setData(
        self,
        index: _QtIndex,
        value: object,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if (
            index.isValid()
            and role == Qt.ItemDataRole.CheckStateRole
            and index.column() == COL_CHECK
        ):
            self._rows[index.row()].checked = value == Qt.CheckState.Checked.value
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
            self.selection_changed.emit()
            return True
        return False

    # --- public surface used by the main window ------------------------

    def replace_rows(self, rows: list[CandidateRow]) -> None:
        """Replace the entire row set; used after a scan completes."""
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()
        self.selection_changed.emit()

    def checked_rows(self) -> list[CandidateRow]:
        return [r for r in self._rows if r.checked]

    def total_bytes_checked(self) -> int:
        return sum(r.size_bytes for r in self._rows if r.checked)

    def deletable_checked(self) -> list[CandidateRow]:
        """Subset of checked rows whose Candidate.deletable is True."""
        return [r for r in self.checked_rows() if r.candidate.deletable]

    def is_any_checked_undeletable(self) -> bool:
        return any(not r.candidate.deletable for r in self.checked_rows())

    def select_all(self, checked: bool) -> None:
        if not self._rows:
            return
        for row in self._rows:
            row.checked = checked
        top = self.index(0, COL_CHECK)
        bottom = self.index(len(self._rows) - 1, COL_CHECK)
        self.dataChanged.emit(top, bottom, [Qt.ItemDataRole.CheckStateRole])
        self.selection_changed.emit()

    def get_row(self, row_index: int) -> CandidateRow | None:
        if 0 <= row_index < len(self._rows):
            return self._rows[row_index]
        return None


def make_rows(
    enriched: list[tuple[Candidate, int]],
) -> list[CandidateRow]:
    """Convert ``(Candidate, size)`` tuples from ``enrich_candidates``."""
    return [CandidateRow(candidate=c, size_bytes=s) for c, s in enriched]


__all__ = (
    "COL_CATEGORY",
    "COL_CHECK",
    "COL_PATH",
    "COL_REASON",
    "COL_RISK",
    "COL_SIZE",
    "CandidateRow",
    "CandidateTableModel",
    "RiskLevel",  # re-export for convenience
    "make_rows",
)
