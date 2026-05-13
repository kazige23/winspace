"""winspace main window.

Layout (top to bottom):

* drive bar — C: free-space label, target-drive selector, scan button,
  "include RISKY" checkbox
* table — candidate list (checkbox / path / size / risk / category / reason)
* footer — selection summary + action buttons (Move / Delete / Undo)
* status bar — Qt-managed; shows progress and outcome messages
"""

from __future__ import annotations

import shutil
import string
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from winspace.core.junction import is_junction
from winspace.gui.candidate_model import (
    COL_CATEGORY,
    COL_CHECK,
    COL_PATH,
    COL_REASON,
    COL_RISK,
    COL_SIZE,
    CandidateTableModel,
    make_rows,
)
from winspace.gui.workers import (
    DeleteWorker,
    MoveWorker,
    OperationOutcome,
    ScanWorker,
    UndoLastWorker,
    get_active_manifest_entries,
)
from winspace.version import __version__


def _format_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def _discover_drives() -> list[Path]:
    """All currently mounted drives, in letter order (skipping C:)."""
    drives: list[Path] = []
    for letter in string.ascii_uppercase:
        if letter == "C":
            continue
        path = Path(f"{letter}:\\")
        if path.exists():
            drives.append(path)
    return drives


class MainWindow(QtWidgets.QMainWindow):
    """The top-level winspace window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"winspace {__version__} — Windows 空间释放工具")
        self.resize(1000, 600)

        # State ---------------------------------------------------------
        self._scan_thread: QtCore.QThread | None = None
        self._scan_worker: ScanWorker | None = None
        self._op_thread: QtCore.QThread | None = None
        self._op_worker: QtCore.QObject | None = None

        self.model = CandidateTableModel(self)
        self.model.selection_changed.connect(self._on_selection_changed)

        # UI ------------------------------------------------------------
        central = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        outer.addLayout(self._build_drive_bar())
        outer.addWidget(self._build_table(), stretch=1)
        outer.addLayout(self._build_footer())

        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready / 准备就绪")

        self._refresh_drive_label()
        self._update_undo_button_enabled()
        self._on_selection_changed()

    # --- builders ---------------------------------------------------------

    def _build_drive_bar(self) -> QtWidgets.QHBoxLayout:
        bar = QtWidgets.QHBoxLayout()

        self.c_drive_label = QtWidgets.QLabel()
        self.c_drive_label.setStyleSheet("font-weight: bold;")
        bar.addWidget(self.c_drive_label)

        bar.addStretch(1)

        bar.addWidget(QtWidgets.QLabel("目标盘 / target:"))
        self.drive_combo = QtWidgets.QComboBox()
        self._populate_drive_combo()
        bar.addWidget(self.drive_combo)

        self.include_risky_cb = QtWidgets.QCheckBox("显示 RISKY / show RISKY")
        self.include_risky_cb.setToolTip("勾选后扫描会显示 IM 数据等高风险目录(默认隐藏)")
        bar.addWidget(self.include_risky_cb)

        self.scan_button = QtWidgets.QPushButton("扫描 / Scan")
        self.scan_button.clicked.connect(self._start_scan)
        bar.addWidget(self.scan_button)

        return bar

    def _build_table(self) -> QtWidgets.QTableView:
        view = QtWidgets.QTableView()
        view.setModel(self.model)
        view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        view.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        view.setSortingEnabled(False)  # rows are pre-sorted by size
        view.setAlternatingRowColors(True)
        view.verticalHeader().setVisible(False)
        view.horizontalHeader().setStretchLastSection(True)
        view.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Interactive)
        view.setColumnWidth(COL_CHECK, 36)
        view.setColumnWidth(COL_PATH, 420)
        view.setColumnWidth(COL_SIZE, 90)
        view.setColumnWidth(COL_RISK, 75)
        view.setColumnWidth(COL_CATEGORY, 140)
        view.setColumnWidth(COL_REASON, 260)
        self.table = view
        return view

    def _build_footer(self) -> QtWidgets.QVBoxLayout:
        footer = QtWidgets.QVBoxLayout()

        self.selection_label = QtWidgets.QLabel("已选 0 项, 合计 0 B")
        footer.addWidget(self.selection_label)

        row = QtWidgets.QHBoxLayout()

        self.select_all_button = QtWidgets.QPushButton("全选 / All")
        self.select_all_button.clicked.connect(lambda: self.model.select_all(True))
        row.addWidget(self.select_all_button)

        self.clear_button = QtWidgets.QPushButton("清空 / None")
        self.clear_button.clicked.connect(lambda: self.model.select_all(False))
        row.addWidget(self.clear_button)

        row.addSpacing(20)

        self.move_button = QtWidgets.QPushButton("移动选中 → 目标盘 / Move selected")
        self.move_button.clicked.connect(self._start_move)
        row.addWidget(self.move_button)

        self.delete_button = QtWidgets.QPushButton("删除选中 / Delete selected")
        self.delete_button.setStyleSheet("color: #b22;")
        self.delete_button.clicked.connect(self._start_delete)
        row.addWidget(self.delete_button)

        row.addStretch(1)

        self.undo_button = QtWidgets.QPushButton("撤销最近 / Undo last")
        self.undo_button.clicked.connect(self._start_undo)
        row.addWidget(self.undo_button)

        footer.addLayout(row)
        return footer

    # --- drive bar helpers ----------------------------------------------

    def _populate_drive_combo(self) -> None:
        self.drive_combo.clear()
        for drive in _discover_drives():
            try:
                usage = shutil.disk_usage(drive)
                free_gb = usage.free / (1024**3)
                label = f"{drive}  ({free_gb:.0f} GB free)"
            except OSError:
                label = str(drive)
            self.drive_combo.addItem(label, str(drive))
        if self.drive_combo.count() == 0:
            self.drive_combo.addItem("(no other drives)", "")
            self.drive_combo.setEnabled(False)

    def _refresh_drive_label(self) -> None:
        try:
            usage = shutil.disk_usage(Path("C:\\"))
            used_gb = usage.used / (1024**3)
            total_gb = usage.total / (1024**3)
            pct = (usage.used / usage.total) * 100 if usage.total else 0
            self.c_drive_label.setText(
                f"C 盘 / drive C:  {used_gb:.0f} / {total_gb:.0f} GB 已用 ({pct:.0f}%)"
            )
        except OSError:
            self.c_drive_label.setText("C 盘 / drive C:  (read failed)")

    # --- selection bookkeeping ------------------------------------------

    def _on_selection_changed(self) -> None:
        checked = self.model.checked_rows()
        total = self.model.total_bytes_checked()
        self.selection_label.setText(f"已选 {len(checked)} 项,合计 {_format_size(total)}")

        any_checked = len(checked) > 0
        self.move_button.setEnabled(any_checked)

        # Delete button only enabled when ALL checked candidates are deletable.
        deletable_only = any_checked and not self.model.is_any_checked_undeletable()
        self.delete_button.setEnabled(deletable_only)
        if any_checked and not deletable_only:
            self.delete_button.setToolTip(
                "选中项里有不可删除的目录(例如 Steam 库 / Docker 数据);只能移动不能删"
            )
        else:
            self.delete_button.setToolTip("")

    def _update_undo_button_enabled(self) -> None:
        try:
            count = len(get_active_manifest_entries())
        except Exception:
            count = 0
        self.undo_button.setEnabled(count > 0)
        if count > 0:
            self.undo_button.setText(f"撤销最近 / Undo last  ({count})")
        else:
            self.undo_button.setText("撤销最近 / Undo last")

    # --- scan -----------------------------------------------------------

    def _start_scan(self) -> None:
        if self._scan_thread is not None:
            return  # already running
        self.scan_button.setEnabled(False)
        self.statusBar().showMessage("扫描中… / scanning…")
        self.model.replace_rows([])

        include_risky = self.include_risky_cb.isChecked()
        self._scan_thread = QtCore.QThread(self)
        self._scan_worker = ScanWorker(include_risky=include_risky)
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.failed.connect(self._on_scan_failed)
        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_worker.failed.connect(self._scan_thread.quit)
        self._scan_thread.finished.connect(self._scan_cleanup)
        self._scan_thread.start()

    @QtCore.Slot(list)
    def _on_scan_finished(self, enriched: list[tuple[object, int]]) -> None:
        rows = make_rows(enriched)  # type: ignore[arg-type]
        self.model.replace_rows(rows)
        self.statusBar().showMessage(
            f"扫描完成,找到 {len(rows)} 项 / scan complete: {len(rows)} candidates"
        )

    @QtCore.Slot(str)
    def _on_scan_failed(self, msg: str) -> None:
        self.statusBar().showMessage(f"扫描失败 / scan failed: {msg}")
        QtWidgets.QMessageBox.warning(self, "winspace", f"扫描失败:\n{msg}")

    def _scan_cleanup(self) -> None:
        if self._scan_thread is not None:
            self._scan_thread.deleteLater()
            self._scan_thread = None
        if self._scan_worker is not None:
            self._scan_worker.deleteLater()
            self._scan_worker = None
        self.scan_button.setEnabled(True)
        self._refresh_drive_label()
        self._update_undo_button_enabled()

    # --- move / delete --------------------------------------------------

    def _selected_paths(self) -> list[Path]:
        return [r.candidate.path for r in self.model.checked_rows()]

    def _selected_total(self) -> int:
        return self.model.total_bytes_checked()

    def _start_move(self) -> None:
        if self._op_thread is not None:
            return
        paths = self._selected_paths()
        if not paths:
            return
        drive_str = self.drive_combo.currentData()
        if not drive_str:
            QtWidgets.QMessageBox.warning(
                self, "winspace", "请选择一个目标盘 / pick a target drive"
            )
            return
        to_drive = Path(drive_str)

        if not self._confirm_action(
            "移动选中目录",
            f"将把 {len(paths)} 个目录 ({_format_size(self._selected_total())}) "
            f"移动到 {to_drive}。\n\n原路径会变成 junction,程序仍可透明访问。\n继续?",
        ):
            return

        self._start_op_worker(MoveWorker(paths, to_drive), label="移动")

    def _start_delete(self) -> None:
        if self._op_thread is not None:
            return
        rows = self.model.deletable_checked()
        paths = [r.candidate.path for r in rows]
        total_bytes = sum(r.size_bytes for r in rows)
        if not paths:
            return
        if not self._confirm_action(
            "彻底删除选中目录",
            f"将永久删除 {len(paths)} 个目录 ({_format_size(total_bytes)})。\n\n"
            "这个操作不可撤销!被删的目录里所有文件都会消失。\n"
            "缓存类目录可由对应工具(npm/pip/浏览器)在使用时自动重建。\n\n继续?",
            destructive=True,
        ):
            return

        self._start_op_worker(DeleteWorker(paths), label="删除")

    def _start_op_worker(self, worker: QtCore.QObject, *, label: str) -> None:
        self.move_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.statusBar().showMessage(f"{label}中… / {label} in progress…")

        self._op_thread = QtCore.QThread(self)
        self._op_worker = worker
        worker.moveToThread(self._op_thread)
        self._op_thread.started.connect(worker.run)  # type: ignore[attr-defined]
        worker.progress.connect(self._on_op_progress)  # type: ignore[attr-defined]
        worker.finished.connect(self._on_op_finished)  # type: ignore[attr-defined]
        worker.finished.connect(self._op_thread.quit)  # type: ignore[attr-defined]
        self._op_thread.finished.connect(self._op_cleanup)
        self._op_thread.start()

    @QtCore.Slot(int, int, Path)
    def _on_op_progress(self, done: int, total: int, current: Path) -> None:
        self.statusBar().showMessage(f"[{done}/{total}] {current}")

    @QtCore.Slot(list)
    def _on_op_finished(self, outcomes: list[OperationOutcome]) -> None:
        ok = [o for o in outcomes if o.success]
        bad = [o for o in outcomes if not o.success]
        freed = sum(o.freed_bytes for o in ok)
        summary_lines = [
            f"成功 {len(ok)} 项, 失败 {len(bad)} 项, 涉及 {_format_size(freed)}.",
            "",
        ]
        for o in ok:
            summary_lines.append(f"  ✓ {o.path} — {o.message}")
        for o in bad:
            summary_lines.append(f"  ✗ {o.path} — {o.message}")
        summary = "\n".join(summary_lines)

        # Box opens AFTER the cleanup hook has run, so the buttons are
        # already re-enabled when the user dismisses it.
        QtCore.QTimer.singleShot(
            0, lambda: QtWidgets.QMessageBox.information(self, "winspace", summary)
        )
        # Re-scan to reflect new state (junctions appear / rows disappear).
        QtCore.QTimer.singleShot(50, self._start_scan)

    def _op_cleanup(self) -> None:
        if self._op_thread is not None:
            self._op_thread.deleteLater()
            self._op_thread = None
        if self._op_worker is not None:
            self._op_worker.deleteLater()
            self._op_worker = None
        self.move_button.setEnabled(True)
        self.delete_button.setEnabled(True)
        self.scan_button.setEnabled(True)
        self._refresh_drive_label()
        self._update_undo_button_enabled()
        self._on_selection_changed()

    # --- undo -----------------------------------------------------------

    def _start_undo(self) -> None:
        if self._op_thread is not None:
            return
        if not self._confirm_action(
            "撤销最近一次迁移",
            "把最近一次 winspace move 的目录复制回 C 盘,移除目标盘副本。\n继续?",
        ):
            return
        self.undo_button.setEnabled(False)
        self._op_thread = QtCore.QThread(self)
        self._op_worker = UndoLastWorker()
        worker = self._op_worker
        worker.moveToThread(self._op_thread)
        self._op_thread.started.connect(worker.run)
        worker.finished.connect(self._on_undo_finished)
        worker.failed.connect(self._on_undo_failed)
        worker.finished.connect(self._op_thread.quit)
        worker.failed.connect(self._op_thread.quit)
        self._op_thread.finished.connect(self._op_cleanup)
        self._op_thread.start()

    @QtCore.Slot(object)
    def _on_undo_finished(self, result: object) -> None:
        if result is None:
            return
        size_bytes = getattr(result, "size_bytes", 0)
        path = getattr(result, "restored_path", "")
        self.statusBar().showMessage(f"已撤销 / undone: {path} ({_format_size(size_bytes)})")

    @QtCore.Slot(str)
    def _on_undo_failed(self, msg: str) -> None:
        self.statusBar().showMessage(f"撤销失败 / undo failed: {msg}")
        QtCore.QTimer.singleShot(
            0,
            lambda: QtWidgets.QMessageBox.warning(self, "winspace", f"撤销失败:\n{msg}"),
        )

    # --- confirm dialog -------------------------------------------------

    def _confirm_action(self, title: str, body: str, *, destructive: bool = False) -> bool:
        icon = (
            QtWidgets.QMessageBox.Icon.Warning
            if destructive
            else QtWidgets.QMessageBox.Icon.Question
        )
        box = QtWidgets.QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle(f"winspace — {title}")
        box.setText(body)
        box.setStandardButtons(
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
        return box.exec() == QtWidgets.QMessageBox.StandardButton.Yes


# A couple of names the test suite & app entry point import directly.

__all__ = ("MainWindow", "_discover_drives", "_format_size", "is_junction")
