"""Activity log page: searchable, severity-filtered view of the application log."""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QTableView, QWidget

from .. import config
from ..i18n import t
from .models import LogFilterProxy, LogModel
from .theme import mono_font
from .widgets import LevelDelegate, SearchField, Texts, hbox, headers_changed, label, vbox

# (translation key, minimum level shown)
LEVELS = [("logs.filter.all", logging.DEBUG), ("logs.filter.info", logging.INFO),
          ("logs.filter.warning", logging.WARNING), ("logs.filter.error", logging.ERROR)]


class LogsPage(QWidget):
    def __init__(self, model: LogModel, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.model = model
        self.proxy = LogFilterProxy(self)
        self.proxy.setSourceModel(model)
        self.tx = tx = Texts()
        self.level = QComboBox()
        for text_key, lvl in LEVELS:
            self.level.addItem(t(text_key), lvl)
        self.level.setCurrentIndex(1)
        self.search = SearchField()
        tx.bind(self.search.setPlaceholderText, "logs.search")
        self.search.setMaximumWidth(self.fontMetrics().averageCharWidth() * 40)
        self.level.currentIndexChanged.connect(self._apply)
        self.search.textChanged.connect(self._apply)
        self.counts = label("", "Muted")
        open_log = tx.button("logs.open_file", "open",
                             slot=lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.LOG_FILE))))
        open_log.setToolTip(str(config.LOG_FILE))
        header = hbox(vbox(tx.label("nav.logs", "PageTitle"), self.counts, spacing=2), None, self.level, self.search,
                      open_log)
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

    def retranslate(self) -> None:
        self.tx.retranslate()
        for i, (text_key, _) in enumerate(LEVELS):
            self.level.setItemText(i, t(text_key))
        headers_changed(self.model)
        self.table.viewport().update()
        self._update_counts()

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
        self.counts.setText(" · ".join((t("count.messages", count=self.model.rowCount()),
                                        t("count.warnings", count=warn), t("count.errors", count=err))))
