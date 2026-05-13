"""Smoke tests for the PySide6 GUI.

We verify the window constructs cleanly, the table model accepts rows,
the selection summary updates when rows are checked, and the Delete
button stays disabled when any selected row is non-deletable.

We do NOT exercise the worker threads here — those use the same engine
functions covered by the unit tests; running them via Qt event loops
just adds flakiness without new coverage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from winspace.detectors.base import Candidate, RiskLevel
from winspace.gui.candidate_model import (
    COL_CHECK,
    CandidateRow,
    CandidateTableModel,
    make_rows,
)


def _safe_candidate(path: str) -> Candidate:
    return Candidate(
        path=Path(path),
        category="node_modules",
        risk=RiskLevel.SAFE,
        reason_zh="可重建",
        reason_en="regenerable",
        detector_name="node_modules",
        deletable=True,
    )


def _undeletable_candidate(path: str) -> Candidate:
    return Candidate(
        path=Path(path),
        category="steam",
        risk=RiskLevel.CONFIRM,
        reason_zh="游戏库",
        reason_en="game library",
        detector_name="steam",
        deletable=False,
    )


# --- model behaviour --------------------------------------------------------


def test_model_starts_empty(qtbot: pytest.FixtureRequest) -> None:
    model = CandidateTableModel()
    assert model.rowCount() == 0
    assert model.columnCount() == 6
    assert model.checked_rows() == []
    assert model.total_bytes_checked() == 0


def test_replace_rows_repopulates(qtbot: pytest.FixtureRequest) -> None:
    model = CandidateTableModel()
    rows = [
        CandidateRow(_safe_candidate("C:\\a"), size_bytes=100),
        CandidateRow(_safe_candidate("C:\\b"), size_bytes=200),
    ]
    model.replace_rows(rows)
    assert model.rowCount() == 2


def test_make_rows_converts_enriched_tuples() -> None:
    enriched = [
        (_safe_candidate("C:\\foo"), 123),
        (_safe_candidate("C:\\bar"), 456),
    ]
    rows = make_rows(enriched)
    assert len(rows) == 2
    assert rows[0].size_bytes == 123
    assert rows[1].size_bytes == 456


def test_select_all_and_clear_toggle_check_state(
    qtbot: pytest.FixtureRequest,
) -> None:
    model = CandidateTableModel()
    model.replace_rows(
        [
            CandidateRow(_safe_candidate("C:\\a"), size_bytes=100),
            CandidateRow(_safe_candidate("C:\\b"), size_bytes=200),
        ]
    )
    model.select_all(True)
    assert len(model.checked_rows()) == 2
    assert model.total_bytes_checked() == 300

    model.select_all(False)
    assert model.checked_rows() == []


def test_deletable_subset_filters_non_deletable(
    qtbot: pytest.FixtureRequest,
) -> None:
    model = CandidateTableModel()
    model.replace_rows(
        [
            CandidateRow(_safe_candidate("C:\\a"), size_bytes=100, checked=True),
            CandidateRow(
                _undeletable_candidate("D:\\Steam\\steamapps"),
                size_bytes=200,
                checked=True,
            ),
        ]
    )
    deletable = model.deletable_checked()
    assert [r.candidate.path for r in deletable] == [Path("C:\\a")]
    assert model.is_any_checked_undeletable() is True


def test_selection_changed_signal_emits_on_select_all(
    qtbot: pytest.FixtureRequest,
) -> None:
    model = CandidateTableModel()
    model.replace_rows([CandidateRow(_safe_candidate("C:\\a"), 100)])
    with qtbot.waitSignal(model.selection_changed, timeout=200):
        model.select_all(True)


# --- main window construction ------------------------------------------------


def test_main_window_constructs(qtbot: pytest.FixtureRequest) -> None:
    """Smoke: can we instantiate the window without crashing?"""
    from winspace.gui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)  # type: ignore[attr-defined]
    assert win.windowTitle().startswith("winspace")
    # Initially: no checked rows, so move/delete buttons are disabled.
    assert win.move_button.isEnabled() is False
    assert win.delete_button.isEnabled() is False


def test_main_window_delete_button_flips_with_selection(
    qtbot: pytest.FixtureRequest,
) -> None:
    """Check that selecting a deletable row enables the Delete button,
    and selecting an undeletable row disables it again.
    """
    from winspace.gui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)  # type: ignore[attr-defined]

    win.model.replace_rows(
        [
            CandidateRow(_safe_candidate("C:\\a"), 100),
            CandidateRow(_undeletable_candidate("D:\\Steam"), 200),
        ]
    )

    win.model.select_all(True)
    # Both selected — one is undeletable, so Delete must be disabled.
    assert win.move_button.isEnabled() is True
    assert win.delete_button.isEnabled() is False

    # Uncheck the undeletable row by toggling its check state via setData.
    from PySide6.QtCore import Qt

    idx = win.model.index(1, COL_CHECK)
    win.model.setData(idx, Qt.CheckState.Unchecked.value, Qt.ItemDataRole.CheckStateRole)
    assert win.delete_button.isEnabled() is True
