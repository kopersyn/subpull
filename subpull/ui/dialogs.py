"""Modal dialogs: download confirmation, unrecognized files, host key and error prompts."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QHeaderView, QMessageBox, QTableView, QWidget)

from ..connection import AuthFailed, HostKeyError
from ..local_state import LocalStatus
from ..workflow import OVERWRITE_STATES, DownloadPlan, fmt_size
from .theme import T
from .widgets import Card, SearchField, button, hbox, label, vbox


class _PlanModel(QAbstractTableModel):
    COLUMNS = ["Object", "Files", "Size", "Skipped", "Saved to"]

    def __init__(self, plan: DownloadPlan):
        super().__init__()
        self.plan = plan

    def set_plan(self, plan):
        self.beginResetModel()
        self.plan = plan
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.plan.rows)

    def columnCount(self, parent=QModelIndex()):
        return 5

    def headerData(self, s, o, role=Qt.ItemDataRole.DisplayRole):
        return self.COLUMNS[s] if o == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole else None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        r = self.plan.rows[index.row()]
        c = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            dest = r.dest_error or (f"{r.dest}" + ("   · new folder" if r.new_folder else ""))
            return [r.object_name, str(r.count), fmt_size(r.size), str(r.skipped) if r.skipped else "", dest][c]
        if role == Qt.ItemDataRole.ForegroundRole:
            if c == 4 and r.dest_error:
                return QColor(T["danger"])
            if c in (3, 4):
                return QColor(T["muted"])
        if role == Qt.ItemDataRole.TextAlignmentRole and c in (1, 2, 3):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None


class ConfirmDownloadDialog(QDialog):
    """Shows exactly what will be downloaded and where. Overwrites are opt-in and confirmed twice."""

    OPTIONS = [
        (LocalStatus.PRESENT, "Download again files already in the library (same size) – overwrites them"),
        (LocalStatus.CONFLICT, "Replace local files whose size differs – overwrites them"),
        (LocalStatus.HISTORY_ONLY, "Download again files from history that are no longer on disk"),
    ]

    def __init__(self, ctl, parent: QWidget = None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("Confirm download")
        self.setModal(True)
        fm = self.fontMetrics()
        self.resize(fm.averageCharWidth() * 130, fm.height() * 30)
        base = ctl.plan()
        self.checks: dict[LocalStatus, QCheckBox] = {}
        opts = []
        for st, text in self.OPTIONS:
            n = base.counts.get(st, 0)
            if n:
                cb = QCheckBox(f"{text}  ({n})")
                cb.toggled.connect(self.refresh)
                self.checks[st] = cb
                opts.append(cb)
        clash = base.counts.get(LocalStatus.NAME_CLASH, 0)
        clash_lbl = label(f"{clash} files are skipped: another scanned file has the same name for that object, or the "
                          "name is not valid as a file name. Names are never changed automatically.", wrap=True)
        clash_lbl.setStyleSheet(f"color: {T['danger']};")
        clash_lbl.setVisible(bool(clash))

        self.model = _PlanModel(base)
        table = QTableView()
        table.setModel(self.model)
        table.verticalHeader().hide()
        table.setShowGrid(False)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        cw = fm.averageCharWidth()
        for i, w in enumerate((22, 8, 11, 9)):
            table.setColumnWidth(i, cw * w)

        self.summary = label("", "SectionTitle")
        self.note = label("", "Muted", wrap=True)
        self.btn_ok = button("Download", "download", "Primary", slot=self._accept)
        self.btn_ok.setDefault(True)
        options_card = Card()
        options_card.setLayout(vbox(label("Files already handled", "SectionTitle"),
                                      label("By default SubPull skips files that are already in your library or in the "
                                            "download history.", "Muted", wrap=True), *opts, clash_lbl,
                                      spacing=6, margins=(14, 12, 14, 12)))
        options_card.setVisible(bool(opts) or bool(clash))
        self.setLayout(vbox(label("Review download", "PageTitle"),
                            label(f"Destination: {ctl.dest_root}", "Muted", selectable=True),
                            options_card, table, self.summary, self.note,
                            hbox(None, button("Cancel", slot=self.reject), self.btn_ok),
                            spacing=10, margins=(20, 18, 20, 18)))
        self.refresh()

    def include(self) -> set[LocalStatus]:
        return {st for st, cb in self.checks.items() if cb.isChecked()}

    def refresh(self, *_):
        self.plan = self.ctl.plan(self.include())
        self.model.set_plan(self.plan)
        queued = sum(1 for _, why in self.plan.skipped if why == "already in the transfer queue")
        self.summary.setText(f"{len(self.plan.todo)} files · {fmt_size(self.plan.size)}")
        notes = [f"{len(self.plan.skipped)} skipped"] if self.plan.skipped else []
        if queued:
            notes.append(f"{queued} already queued")
        if self.plan.overwrite_count:
            notes.append(f"{self.plan.overwrite_count} existing files will be overwritten")
        self.note.setText(" · ".join(notes))
        self.note.setStyleSheet(f"color: {T['warning']};" if self.plan.overwrite_count else "")
        self.btn_ok.setText(f"Download {len(self.plan.todo)} files" if self.plan.todo else "Nothing to download")
        self.btn_ok.setEnabled(bool(self.plan.todo))

    def overwrite_states(self) -> set[LocalStatus]:
        return self.include() & set(OVERWRITE_STATES)

    def _accept(self):
        if self.plan.overwrite_count:
            r = QMessageBox.warning(self, "Overwrite files",
                                    f"{self.plan.overwrite_count} files that already exist in your library will be "
                                    "replaced by the server version.\n\nContinue?",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                    QMessageBox.StandardButton.Cancel)
            if r != QMessageBox.StandardButton.Yes:
                return
        self.accept()


class _UnrecModel(QAbstractTableModel):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 3

    def headerData(self, s, o, role=Qt.ItemDataRole.DisplayRole):
        return ["Path", "Size", "Reason"][s] if o == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole else None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        u = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return [u.remote_path, fmt_size(u.size) if u.size else "", u.reason][index.column()]
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 2:
            return QColor(T["warning"])
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{u.remote_path}\n{u.reason}"
        return None


class UnrecognizedDialog(QDialog):
    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unrecognized items")
        fm = self.fontMetrics()
        self.resize(fm.averageCharWidth() * 150, fm.height() * 30)
        from PySide6.QtCore import QSortFilterProxyModel
        model = _UnrecModel(result.unrecognized)
        proxy = QSortFilterProxyModel(self)
        proxy.setSourceModel(model)
        proxy.setFilterKeyColumn(-1)
        proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        search = SearchField("Filter")
        search.textChanged.connect(proxy.setFilterFixedString)
        table = QTableView()
        table.setModel(proxy)
        table.verticalHeader().hide()
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setSortingEnabled(True)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        table.setColumnWidth(0, fm.averageCharWidth() * 80)
        table.setColumnWidth(1, fm.averageCharWidth() * 10)
        self.setLayout(vbox(
            label(f"{len(result.unrecognized)} unrecognized items", "PageTitle"),
            label("These files have no unambiguous object (for example OBJECT = 'Target' in a folder without an object "
                  "name) or are folders that are not frame types. SubPull never downloads them, so nothing ends up in a "
                  "random folder.", "Muted", wrap=True),
            search, table, hbox(None, button("Close", slot=self.accept)), spacing=10, margins=(20, 18, 20, 18)))


def show_error(parent, exc: Exception, retry) -> None:
    """Explain a connection problem; offers retry where it can help, trust-once for unknown host keys."""
    if isinstance(exc, HostKeyError):
        if exc.mismatch:
            QMessageBox.critical(parent, "Host key mismatch",
                                 f"{exc}\n\nThe server identifies itself with a different key than the one FileZilla "
                                 "trusts. The connection was blocked. Check the server in FileZilla before continuing.")
            return
        box = QMessageBox(QMessageBox.Icon.Warning, "Unknown host key",
                          f"{exc}\n\nThis key is not stored in FileZilla or known_hosts. Trust it for this session "
                          "only? (Nothing is saved.)", QMessageBox.StandardButton.NoButton, parent)
        trust = box.addButton("Trust for this session", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is trust and retry:
            parent.ctl.conn.accept_hostkey_for_session(exc.key_blob)
            retry()
        return
    if isinstance(exc, AuthFailed) or retry is None:
        QMessageBox.critical(parent, "Connection problem", str(exc))
        return
    r = QMessageBox.warning(parent, "Connection problem", f"{exc}\n\nTry again?",
                            QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Cancel,
                            QMessageBox.StandardButton.Retry)
    if r == QMessageBox.StandardButton.Retry:
        retry()
