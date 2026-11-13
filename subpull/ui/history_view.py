"""History page: downloaded files (searchable) and the activity log of scans, batches and errors."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QAbstractItemView, QSplitter, QTableView, QWidget

from .controller import Controller
from .models import HistoryModel, OperationsModel, TextFilterProxy
from .widgets import SearchField, TypeBadgeDelegate, button, hbox, label, vbox


def _table(model) -> QTableView:
    t = QTableView()
    t.setModel(model)
    t.verticalHeader().hide()
    t.setShowGrid(False)
    t.setWordWrap(False)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    t.horizontalHeader().setStretchLastSection(True)
    t.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    t.verticalHeader().setDefaultSectionSize(int(t.fontMetrics().height() * 1.8))
    return t


class HistoryPage(QWidget):
    def __init__(self, ctl: Controller, qs: QSettings, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.ctl = ctl
        self.qs = qs
        self.count_label = label("", "Muted")
        self.search = SearchField("Search object, file or session")
        self.search.setMaximumWidth(self.fontMetrics().averageCharWidth() * 40)
        self.btn_open = button("Show in folder", "open", slot=self._open_selected)
        self.btn_open.setEnabled(False)
        header = hbox(vbox(label("History", "PageTitle"), self.count_label, spacing=2), None, self.search, self.btn_open)

        self.model = HistoryModel(self)
        self.proxy = TextFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.table = _table(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
        self.table.setItemDelegateForColumn(3, TypeBadgeDelegate(self.table))
        self.table.doubleClicked.connect(lambda _: self._open_selected())
        self.table.selectionModel().selectionChanged.connect(
            lambda *_: self.btn_open.setEnabled(bool(self.table.selectionModel().selectedRows())))
        cw = self.fontMetrics().averageCharWidth()
        for i, w in enumerate((17, 16, 44, 11, 10, 12, 12, 60)):
            self.table.setColumnWidth(i, cw * w)

        self.ops_model = OperationsModel(self)
        self.ops = _table(self.ops_model)
        for i, w in enumerate((17, 10)):
            self.ops.setColumnWidth(i, cw * w)

        downloads = QWidget()
        downloads.setLayout(vbox(label("Downloaded files", "SectionTitle"), self.table, spacing=8))
        activity = QWidget()
        activity.setLayout(vbox(label("Activity", "SectionTitle"),
                                label("Scans, download batches and transfer errors.", "Muted"), self.ops, spacing=8))
        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.addWidget(downloads)
        self.split.addWidget(activity)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 1)
        self.split.setChildrenCollapsible(False)
        if qs.contains("history/split"):
            self.split.restoreState(qs.value("history/split"))

        self.setLayout(vbox(header, self.split, spacing=12, margins=(20, 16, 20, 16)))
        ctl.historyChanged.connect(self.refresh)
        self.refresh()

    def save_state(self) -> None:
        self.qs.setValue("history/split", self.split.saveState())

    def refresh(self) -> None:
        rows = self.ctl.history.list_downloads(limit=20000)
        self.model.set_rows(rows)
        self.ops_model.set_rows(self.ctl.history.list_operations(limit=1000))
        total = sum(r["size"] or 0 for r in rows)
        from ..workflow import fmt_size
        self.count_label.setText(f"{len(rows)} files downloaded · {fmt_size(total)}" if rows else "Nothing downloaded yet")

    def _open_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        r = self.model.row_at(self.proxy.mapToSource(rows[0]).row())
        p = Path(r["local_path"])
        folder = p.parent if p.parent.exists() else None
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
