"""GUI entry point.

Invoked by ``winspace`` with no subcommand (see :mod:`winspace.cli`) or
by ``python -m winspace.gui``.
"""

from __future__ import annotations

import sys

from PySide6 import QtWidgets

from winspace.gui.main_window import MainWindow


def run_gui(argv: list[str] | None = None) -> int:
    """Construct the QApplication, show the window, run the event loop."""
    args = argv if argv is not None else sys.argv
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(args)
    app.setApplicationName("winspace")
    win = MainWindow()
    win.show()
    return int(app.exec())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_gui())
