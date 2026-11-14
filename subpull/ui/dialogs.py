"""Modal dialogs: download confirmation, unrecognized files, host key and error prompts."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QHeaderView, QMessageBox, QTableView, QWidget)

from ..connection import AuthFailed, HostKeyError
from ..i18n import t
from ..local_state import LocalStatus
from ..workflow import OVERWRITE_STATES, SKIP_QUEUED, DownloadPlan, fmt_size
from .theme import T
from .widgets import Card, SearchField, button, hbox, label, vbox


_ROLES = {"ok": QMessageBox.ButtonRole.AcceptRole, "yes": QMessageBox.ButtonRole.YesRole,
          "retry": QMessageBox.ButtonRole.AcceptRole, "no": QMessageBox.ButtonRole.NoRole,
          "cancel": QMessageBox.ButtonRole.RejectRole}


def message(parent, icon, title: str, text: str, buttons=("ok",), default: str = "", extra: str = "") -> str:
    """Modal message box whose buttons are labelled in the selected language. ``buttons`` are
    names from ``actions.*`` (ok, yes, no, retry, cancel); ``extra`` is the text of an additional
    accept button. Returns the name of the clicked button ("extra" for the additional one);
    closing the box counts as its last button."""
    box = QMessageBox(icon, title, text, QMessageBox.StandardButton.NoButton, parent)
    made = {name: box.addButton(t(f"actions.{name}"), _ROLES[name]) for name in buttons}
    if extra:
        made = {"extra": box.addButton(extra, QMessageBox.ButtonRole.AcceptRole), **made}
    last = list(made)[-1]
    box.setEscapeButton(made[last])
    if default in made:
        box.setDefaultButton(made[default])
    box.exec()
    clicked = box.clickedButton()
    return next((name for name, b in made.items() if b is clicked), last)


class _PlanModel(QAbstractTableModel):
    COLUMNS = ["column.object", "column.files", "column.size", "column.skipped", "column.saved_to"]

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
        return t(self.COLUMNS[s]) if o == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole else None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        r = self.plan.rows[index.row()]
        c = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            dest = r.dest_error or (f"{r.dest}" + (f"   · {t('confirm.new_folder')}" if r.new_folder else ""))
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
        (LocalStatus.PRESENT, "confirm.option.present"),
        (LocalStatus.CONFLICT, "confirm.option.conflict"),
        (LocalStatus.HISTORY_ONLY, "confirm.option.history_only"),
    ]

    def __init__(self, ctl, parent: QWidget = None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle(t("confirm.window_title"))
        self.setModal(True)
        fm = self.fontMetrics()
        self.resize(fm.averageCharWidth() * 130, fm.height() * 30)
        base = ctl.plan()
        self.checks: dict[LocalStatus, QCheckBox] = {}
        opts = []
        for st, text in self.OPTIONS:
            n = base.counts.get(st, 0)
            if n:
                cb = QCheckBox(f"{t(text)}  ({n})")
                cb.toggled.connect(self.refresh)
                self.checks[st] = cb
                opts.append(cb)
        clash = base.counts.get(LocalStatus.NAME_CLASH, 0)
        clash_lbl = label(t("confirm.clash", count=clash), wrap=True)
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
        self.btn_ok = button(t("actions.download"), "download", "Primary", slot=self._accept)
        self.btn_ok.setDefault(True)
        options_card = Card()
        options_card.setLayout(vbox(label(t("confirm.handled.title"), "SectionTitle"),
                                      label(t("confirm.handled.text"), "Muted", wrap=True), *opts, clash_lbl,
                                      spacing=6, margins=(14, 12, 14, 12)))
        options_card.setVisible(bool(opts) or bool(clash))
        self.setLayout(vbox(label(t("confirm.title"), "PageTitle"),
                            label(t("confirm.destination", path=ctl.dest_root), "Muted", selectable=True),
                            options_card, table, self.summary, self.note,
                            hbox(None, button(t("actions.cancel"), slot=self.reject), self.btn_ok),
                            spacing=10, margins=(20, 18, 20, 18)))
        self.refresh()

    def include(self) -> set[LocalStatus]:
        return {st for st, cb in self.checks.items() if cb.isChecked()}

    def refresh(self, *_):
        self.plan = self.ctl.plan(self.include())
        self.model.set_plan(self.plan)
        queued = sum(1 for _, why in self.plan.skipped if why == SKIP_QUEUED)
        self.summary.setText(f"{t('count.files', count=len(self.plan.todo))} · {fmt_size(self.plan.size)}")
        notes = [t("count.skipped", count=len(self.plan.skipped))] if self.plan.skipped else []
        if queued:
            notes.append(t("confirm.note.queued", count=queued))
        if self.plan.overwrite_count:
            notes.append(t("confirm.note.overwrite", count=self.plan.overwrite_count))
        self.note.setText(" · ".join(notes))
        self.note.setStyleSheet(f"color: {T['warning']};" if self.plan.overwrite_count else "")
        self.btn_ok.setText(t("actions.download_count", count=len(self.plan.todo)) if self.plan.todo
                            else t("confirm.nothing"))
        self.btn_ok.setEnabled(bool(self.plan.todo))

    def overwrite_states(self) -> set[LocalStatus]:
        return self.include() & set(OVERWRITE_STATES)

    def _accept(self):
        if self.plan.overwrite_count:
            if message(self, QMessageBox.Icon.Warning, t("confirm.overwrite.title"),
                       t("confirm.overwrite.text", count=self.plan.overwrite_count),
                       ("yes", "cancel"), default="cancel") != "yes":
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
        if o == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return t(("column.path", "column.size", "column.reason")[s])
        return None

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
        self.setWindowTitle(t("unrecognized.window_title"))
        fm = self.fontMetrics()
        self.resize(fm.averageCharWidth() * 150, fm.height() * 30)
        from PySide6.QtCore import QSortFilterProxyModel
        model = _UnrecModel(result.unrecognized)
        proxy = QSortFilterProxyModel(self)
        proxy.setSourceModel(model)
        proxy.setFilterKeyColumn(-1)
        proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        search = SearchField(t("unrecognized.filter"))
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
            label(t("unrecognized.title", count=len(result.unrecognized)), "PageTitle"),
            label(t("unrecognized.text"), "Muted", wrap=True),
            search, table, hbox(None, button(t("actions.close"), slot=self.accept)), spacing=10, margins=(20, 18, 20, 18)))


def show_error(parent, exc: Exception, retry) -> None:
    """Explain a connection problem; offers retry where it can help, trust-once for unknown host keys."""
    if isinstance(exc, HostKeyError):
        if exc.mismatch:
            message(parent, QMessageBox.Icon.Critical, t("hostkey.mismatch.title"),
                    f"{exc}\n\n{t('hostkey.mismatch.text')}")
            return
        clicked = message(parent, QMessageBox.Icon.Warning, t("hostkey.unknown.title"),
                          f"{exc}\n\n{t('hostkey.unknown.text')}", ("cancel",), extra=t("hostkey.unknown.trust"))
        if clicked == "extra" and retry:
            parent.ctl.conn.accept_hostkey_for_session(exc.key_blob)
            retry()
        return
    if isinstance(exc, AuthFailed) or retry is None:
        message(parent, QMessageBox.Icon.Critical, t("error.connection.title"), str(exc))
        return
    if message(parent, QMessageBox.Icon.Warning, t("error.connection.title"), f"{exc}\n\n{t('error.connection.retry')}",
               ("retry", "cancel"), default="retry") == "retry":
        retry()
