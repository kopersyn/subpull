"""Activity log page: searchable, severity-filtered view of the application log."""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QTableView, QWidget

from .. import config
from .models import LogFilterProxy, LogModel
from .theme import mono_font
from .widgets import LevelDelegate, SearchField, button, hbox, label, vbox

LEVELS = [("All messages", logging.DEBUG), ("Info and above", logging.INFO), ("Warnings and errors", logging.WARNING),
          ("Errors only", logging.ERROR)]


class LogsPage(QWidget):
    def __init__(self, model: LogModel, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.model = model
        self.proxy = LogFilterProxy(self)
        self.proxy.setSourceModel(model)
        self.level = QComboBox()
        for text, lvl in LEVELS:
            self.level.addItem(text, lvl)
        self.level.setCurrentIndex(1)
        self.search = SearchField("Search messages")
        self.search.setMaximumWidth(self.fontMetrics().averageCharWidth() * 40)
        self.level.currentIndexChanged.connect(self._apply)
        self.search.textChanged.connect(self._apply)
        self.counts = label("", "Muted")
        header = hbox(vbox(label("Activity log", "PageTitle"), self.counts, spacing=2), None, self.level, self.search,
                      button("Open log file", "open", tooltip=str(config.LOG_FILE),
                             slot=lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.LOG_FILE)))))
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setItemDelegateForColumn(1, LevelDelegate(1, self.table))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.verticalHeader().setDefaultSectionSize(int(self.fontMetrics().height() * 1.7))
        cw = self.fontMetrics().averageCharWidth()
        for i, w in enumerate((10, 12, 14)):
            self.table.setColumnWidth(i, cw * w)
        self.setLayout(vbox(header, self.table, spacing=12, margins=(20, 16, 20, 16)))
        model.rowsInserted.connect(self._on_rows)
        self._apply()

    def _apply(self, *_):
        self.proxy.set_filter(self.level.currentData(), self.search.text().strip())
        self._update_counts()

    def _on_rows(self, *_):
        self._update_counts()
        sb = self.table.verticalScrollBar()
        if sb.value() >= sb.maximum() - 4:
            self.table.scrollToBottom()

    def _update_counts(self):
        err, warn = self.model.counts()
        self.counts.setText(f"{self.model.rowCount()} messages · {warn} warnings · {err} errors")
