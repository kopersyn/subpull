"""Library page: objects list, frame browser with filters, and a details panel."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QItemSelectionModel, QModelIndex, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QHeaderView, QLabel,
                               QListView, QMenu, QPushButton, QScrollArea, QSplitter, QStackedWidget,
                               QToolButton, QTreeView, QVBoxLayout, QWidget)

from ..i18n import t
from ..local_state import LocalStatus, status_label
from ..scanner import fits_status_label
from ..workflow import (UNKNOWN_TYPE, FileFilter, exposure_seconds, filter_files, fmt_seconds, fmt_size,
                        scan_summary)
from . import icons
from .controller import Controller
from .models import FileRole, FrameTreeModel, GroupRole, ObjectListModel
from .theme import T, TYPE_COLORS, mono_font
from .widgets import (Card, EmptyState, FrameDelegate, ObjectDelegate, SearchField, Segmented, Texts, TypeChip,
                      button, hbox, headers_changed, label, vbox)

# (frame type, translation key of the chip label)
TYPE_CHIPS = [("LIGHT", "frame_type.light"), ("FLAT", "frame_type.flat"), ("DARK", "frame_type.dark"),
              ("DARKFLAT", "frame_type.darkflat"), ("BIAS", "frame_type.bias"), (UNKNOWN_TYPE, "frame_type.other")]
DEFAULT_HIDDEN = {"object", "notes"}


class LibraryPage(QWidget):
    downloadRequested = Signal()
    unrecognizedRequested = Signal()
    chooseDestination = Signal()
    scanRequested = Signal()

    def __init__(self, ctl: Controller, ui_settings: QSettings, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.ctl = ctl
        self.qs = ui_settings
        self._current_object: Optional[str] = None
        self._collapsed: set[str] = set()
        self._filter_timer = QTimer(self, singleShot=True, interval=160, timeout=self.refresh_files)
        self.tx = tx = Texts()

        # ---- header ---------------------------------------------------------------
        self.title = tx.label("nav.library", "PageTitle")
        self.subtitle = label("", "Muted")
        self.btn_unrec = button("", "alert", "Warn", slot=self.unrecognizedRequested.emit)
        tx.bind(self.btn_unrec.setToolTip, "library.unrecognized.tooltip")
        self.btn_unrec.hide()
        self.btn_details = tx.tool_button("sidebar", "library.details.tooltip", self._toggle_details)
        self.btn_details.setCheckable(True)
        header = hbox(vbox(self.title, self.subtitle, spacing=2), None, self.btn_unrec, self.btn_details)

        # ---- destination banner ----------------------------------------------------------
        self.banner = Card("Banner")
        self.banner.setLayout(hbox(tx.label("library.banner.text"),
                                   None, tx.button("library.banner.choose", "folder", slot=self.chooseDestination.emit),
                                   margins=(12, 8, 8, 8)))

        # ---- empty state -----------------------------------------------------------------
        self.empty = EmptyState("target", "", "",
                                tx.button("library.empty.action", "scan", "Primary", slot=self.scanRequested.emit))
        tx.bind(self.empty.title.setText, "library.empty.title")
        tx.bind(self.empty.text.setText, "library.empty.text")

        # ---- objects panel ---------------------------------------------------------------
        self.obj_search = SearchField()
        tx.bind(self.obj_search.setPlaceholderText, "library.objects.filter")
        self.obj_search.textChanged.connect(self._on_object_search)
        self.obj_model = ObjectListModel(self)
        self.obj_view = QListView()
        self.obj_view.setModel(self.obj_model)
        self.obj_view.setItemDelegate(ObjectDelegate(self.obj_view))
        self.obj_view.setMouseTracking(True)
        self.obj_view.setUniformItemSizes(True)
        self.obj_view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.obj_view.selectionModel().currentChanged.connect(self._on_object_changed)
        objects = QWidget()
        objects.setLayout(vbox(tx.label("library.objects.title", "SectionTitle"), self.obj_search, self.obj_view,
                               spacing=8))

        # ---- filter bar ------------------------------------------------------------------
        self.group_seg = Segmented([("object", "library.group.object"), ("session", "library.group.session"),
                                    ("type", "library.group.type")], self.qs.value("library/group", "object"))
        self.group_seg.changed.connect(self._on_group_changed)
        self.chips = [TypeChip(frame_type) for frame_type, _ in TYPE_CHIPS]
        for c, (_, text_key) in zip(self.chips, TYPE_CHIPS):
            tx.bind(c.setText, text_key)
            c.toggled.connect(lambda _=False: self.refresh_files())
        self.session_cb = QComboBox()
        self.session_cb.setMinimumContentsLength(11)
        self.session_cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.session_cb.currentIndexChanged.connect(lambda _=0: self.refresh_files())
        self.new_only = QCheckBox()
        tx.bind(self.new_only.setText, "library.new_only")
        tx.bind(self.new_only.setToolTip, "library.new_only.tooltip")
        self.new_only.toggled.connect(lambda _=False: self.refresh_files())
        self.file_search = SearchField()
        tx.bind(self.file_search.setPlaceholderText, "library.search_files")
        self.file_search.setMaximumWidth(self.fontMetrics().averageCharWidth() * 30)
        self.file_search.textChanged.connect(lambda _: self._filter_timer.start())
        self.btn_columns = tx.tool_button("columns", "library.columns.tooltip")
        self.btn_columns.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.file_search.setMinimumWidth(self.fontMetrics().averageCharWidth() * 18)
        filters = vbox(hbox(tx.label("library.group.label", "Muted"), self.group_seg, 12, self.session_cb, self.new_only,
                            None, self.btn_columns, spacing=6),
                       hbox(tx.label("library.types.label", "Muted"), *self.chips, 8, None, self.file_search, spacing=4),
                       spacing=8)

        # ---- frame tree ------------------------------------------------------------------
        self.model = FrameTreeModel(ctl.selected, self)
        self.model.selectionChanged.connect(lambda: self.ctl.selectionChanged.emit(False))
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        # connected after setModel so it runs after the view's own reset handling (which clears row spans)
        self.model.modelReset.connect(self._after_reset)
        self.tree.setItemDelegate(FrameDelegate(self.model, self.tree))
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.tree.setMouseTracking(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        self.tree.setIndentation(int(self.fontMetrics().height() * 1.1))
        self.tree.setAllColumnsShowFocus(True)
        self.tree.installEventFilter(self)
        self.tree.viewport().installEventFilter(self)
        self.tree.doubleClicked.connect(self._on_double_click)
        self.tree.expanded.connect(lambda ix: self._collapsed.discard(self.model.group_key_at(ix)))
        self.tree.collapsed.connect(lambda ix: self._collapsed.add(self.model.group_key_at(ix)))
        self.tree.selectionModel().currentChanged.connect(self._update_details)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hdr.setSectionsMovable(True)
        hdr.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr.customContextMenuRequested.connect(lambda pos: self._columns_menu().exec(hdr.mapToGlobal(pos)))
        self._init_columns()
        self.btn_columns.setMenu(self._columns_menu())

        center = QWidget()
        center.setLayout(vbox(filters, self.tree, spacing=8))

        # ---- details panel ---------------------------------------------------------------
        self.details = DetailsPanel(ctl)
        self.details.setMinimumWidth(self.fontMetrics().averageCharWidth() * 34)

        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.addWidget(objects)
        self.split.addWidget(center)
        self.split.addWidget(self.details)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setStretchFactor(2, 0)
        self.split.setChildrenCollapsible(False)
        cw = self.fontMetrics().averageCharWidth()
        objects.setMinimumWidth(cw * 24)
        self.split.setSizes([cw * 34, cw * 140, cw * 42])
        if self.qs.contains("library/split"):
            self.split.restoreState(self.qs.value("library/split"))
        show_details = self.qs.value("library/details", True, type=bool)
        self.details.setVisible(show_details)
        self.btn_details.setChecked(show_details)

        # ---- footer ---------------------------------------------------------------------
        self.sel_label = label("", "Muted")
        self.btn_select = tx.button("library.selection.button", "select")
        self.btn_select.setMenu(self._selection_menu())
        self.btn_headers = tx.button("library.read_headers", "fits", tooltip="library.read_headers.tooltip",
                                     slot=lambda: self.ctl.read_headers_for(list(self.ctl.selected)))
        self.btn_download = button("", "download", "Primary", slot=self.downloadRequested.emit)
        footer = hbox(self.btn_select, self.btn_headers, None, self.sel_label, 12, self.btn_download)

        self.content = QWidget()
        self.content.setLayout(vbox(self.split, footer, spacing=10))
        self.stack = QStackedWidget()
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.content)

        self.setLayout(vbox(header, self.banner, self.stack, spacing=12, margins=(20, 16, 20, 16)))

        # ---- controller signals ----------------------------------------------------------------
        ctl.scanFinished.connect(self.on_scan_finished)
        ctl.statusesChanged.connect(self.on_statuses_changed)
        ctl.pathsChanged.connect(self.on_paths_changed)
        ctl.selectionChanged.connect(self.on_selection_changed)
        ctl.destinationChanged.connect(self._update_banner)
        ctl.headersBusy.connect(lambda busy: self.btn_headers.setEnabled(not busy))
        ctl.historyChanged.connect(self._update_subtitle)
        self._update_banner()
        self._update_subtitle()
        self.on_selection_changed(False)

    def retranslate(self) -> None:
        """Re-apply all texts after a language change (widgets, menus, headers and dynamic labels)."""
        self.tx.retranslate()
        self.group_seg.retranslate()
        for btn, menu in ((self.btn_columns, self._columns_menu()), (self.btn_select, self._selection_menu())):
            old = btn.menu()
            btn.setMenu(menu)
            if old is not None:
                old.deleteLater()
        if self.session_cb.count():
            self.session_cb.setItemText(0, t("library.sessions.all"))
        self._set_unrecognized_count()
        headers_changed(self.model)
        self.tree.viewport().update()
        if self.ctl.result is not None:
            self._refresh_object_counts()
        self._update_subtitle()
        self.on_selection_changed(False)
        self.details.retranslate()
        self._update_details()

    # ================================================================== columns
    def _init_columns(self) -> None:
        hdr = self.tree.header()
        cw = self.fontMetrics().averageCharWidth()
        widths = dict(name=46, type=12, filter=8, exposure=10, size=11, session=12, object=16, local=24, notes=34)
        for i, (key, _) in enumerate(FrameTreeModel.COLUMNS):
            self.tree.setColumnWidth(i, cw * widths[key])
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        hidden = set(self.qs.value("library/hidden_columns", list(DEFAULT_HIDDEN)) or [])
        for i, (key, _) in enumerate(FrameTreeModel.COLUMNS):
            self.tree.setColumnHidden(i, key in hidden)
        if self.qs.contains("library/header"):
            hdr.restoreState(self.qs.value("library/header"))
            for i, (key, _) in enumerate(FrameTreeModel.COLUMNS):
                self.tree.setColumnHidden(i, key in hidden)

    def _columns_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setTitle(t("library.columns.title"))
        for i, (key, title) in enumerate(FrameTreeModel.COLUMNS):
            if key == "name":
                continue
            act = QAction(t(title), menu, checkable=True)
            act.setData(i)
            act.setChecked(not self.tree.isColumnHidden(i))
            act.toggled.connect(lambda on, i=i: self._set_column(i, on))
            menu.addAction(act)

        def sync():
            for a in menu.actions():
                a.blockSignals(True)
                a.setChecked(not self.tree.isColumnHidden(a.data()))
                a.blockSignals(False)
        menu.aboutToShow.connect(sync)
        return menu

    def _set_column(self, i: int, visible: bool) -> None:
        self.tree.setColumnHidden(i, not visible)
        self._fit_name_column()
        hidden = [k for j, (k, _) in enumerate(FrameTreeModel.COLUMNS) if self.tree.isColumnHidden(j)]
        self.qs.setValue("library/hidden_columns", hidden)

    def save_state(self) -> None:
        self.qs.setValue("library/split", self.split.saveState())
        self.qs.setValue("library/header", self.tree.header().saveState())
        self.qs.setValue("library/details", self.details.isVisible())

    # ================================================================== selection menu
    def _selection_menu(self) -> QMenu:
        m = QMenu(self)
        m.addAction(t("library.selection.select_visible"), lambda: self._select_visible(True), QKeySequence("Ctrl+A"))
        m.addAction(t("library.selection.select_new"), self._select_new_visible)
        m.addAction(t("library.selection.clear_visible"), lambda: self._select_visible(False))
        m.addSeparator()
        m.addAction(t("library.selection.select_object"), self._select_current_object)
        m.addAction(t("library.selection.select_session"), self._select_current_session)
        m.addSeparator()
        m.addAction(t("library.selection.select_all"), lambda: self.ctl.select_all(True))
        m.addAction(t("library.selection.clear"), lambda: self.ctl.select_all(False), QKeySequence("Ctrl+Shift+A"))
        return m

    def _select_visible(self, value: bool) -> None:
        self.ctl.set_selected([f.remote_path for f in self.model.visible_files()], value)

    def _select_new_visible(self) -> None:
        new = [f.remote_path for f in self.model.visible_files()
               if self.ctl.statuses.get(f.remote_path, (LocalStatus.NEW,))[0] in (LocalStatus.NEW, LocalStatus.PARTIAL)]
        self.ctl.set_selected(new, True)

    def _current_file(self):
        return self.model.file_at(self.tree.currentIndex().siblingAtColumn(0))

    def _select_current_object(self) -> None:
        obj = self._current_object
        f = self._current_file()
        if f is not None:
            obj = f.object_name
        elif self.model.group_by == "object":
            obj = self.model.group_key_at(self.tree.currentIndex().siblingAtColumn(0)) or obj
        if obj and self.ctl.result:
            self.ctl.set_selected([f.remote_path for f in self.ctl.result.files if f.object_name == obj], True)

    def _select_current_session(self) -> None:
        f = self._current_file()
        sess = f.session_date if f else (self.model.group_key_at(self.tree.currentIndex().siblingAtColumn(0))
                                         if self.model.group_by == "session" else None)
        sess = sess or (self.session_cb.currentData() or None)
        if sess and self.ctl.result:
            obj = self._current_object
            self.ctl.set_selected([x.remote_path for x in self.ctl.result.files
                                   if x.session_date == sess and (obj is None or x.object_name == obj)], True)

    # ================================================================== controller events
    def on_scan_finished(self) -> None:
        res = self.ctl.result
        self.stack.setCurrentIndex(1 if res is not None else 0)
        sessions = sorted({f.session_date for f in res.files}, reverse=True)
        cur = self.session_cb.currentData()
        self.session_cb.blockSignals(True)
        self.session_cb.clear()
        self.session_cb.addItem(t("library.sessions.all"), None)
        for s in sessions:
            self.session_cb.addItem(s, s)
        i = self.session_cb.findData(cur)
        self.session_cb.setCurrentIndex(max(0, i))
        self.session_cb.blockSignals(False)
        self._set_unrecognized_count()
        self._fill_objects()
        self.refresh_files()
        self._update_subtitle()

    def _set_unrecognized_count(self) -> None:
        n = len(self.ctl.result.unrecognized) if self.ctl.result is not None else 0
        self.btn_unrec.setText(t("count.unrecognized", count=n))
        self.btn_unrec.setVisible(n > 0)

    def on_statuses_changed(self) -> None:
        self._fill_objects()
        self.refresh_files()

    def on_paths_changed(self, paths) -> None:
        self.model.refresh_paths(paths)
        self._update_details()
        self._refresh_object_counts()

    def on_selection_changed(self, external: bool) -> None:
        if external:
            self.model.selection_changed_externally()
        n, size = self.ctl.selection_summary()
        self.sel_label.setText(t("library.selection.summary", count=n, size=fmt_size(size)) if n
                               else t("library.selection.none"))
        self.btn_download.setText(t("actions.download_count", count=n) if n else t("actions.download"))
        self.btn_download.setEnabled(n > 0 and not self.ctl.scanning)
        self.btn_headers.setEnabled(n > 0)

    def _update_banner(self) -> None:
        self.banner.setVisible(self.ctl.dest_root is None)

    def _update_subtitle(self) -> None:
        row = self.ctl.last_scan()
        if row is None:
            self.subtitle.setText(t("library.subtitle.never"))
            return
        from datetime import datetime
        when = datetime.fromtimestamp(row["ts"]).strftime("%Y-%m-%d %H:%M")
        if self.ctl.result is not None:        # scanned in this session: describe it in the current language
            summary = scan_summary(self.ctl.result, self.ctl.scan_elapsed)
        else:                                  # from the history database, in the language it was recorded in
            summary = row["summary"].split(": ", 1)[-1]
        self.subtitle.setText(t("library.subtitle.last_scan", when=when, summary=summary))

    # ================================================================== objects
    def _fill_objects(self) -> None:
        q = self.obj_search.text().strip().lower()
        sums = [s for s in self.ctl.object_summaries() if not q or q in s.name.lower()]
        self.obj_model.set_summaries(sums)
        row = self.obj_model.row_of(self._current_object)
        if row < 0 or (self._current_object and self.obj_model.object_at(row) != self._current_object):
            self._current_object = None
            row = 0
        if self.obj_model.rowCount():
            self.obj_view.selectionModel().setCurrentIndex(self.obj_model.index(row),
                                                           QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def _refresh_object_counts(self) -> None:
        self.obj_view.viewport().update()
        cur = self._current_object
        self._fill_objects()
        self._current_object = cur

    def _on_object_search(self, _text: str) -> None:
        self._fill_objects()
        self._filter_timer.start()

    def _on_object_changed(self, current: QModelIndex, _prev) -> None:
        name = self.obj_model.object_at(current.row()) if current.isValid() else None
        if name != self._current_object:
            self._current_object = name
            self.refresh_files()

    def _on_group_changed(self, key: str) -> None:
        self.qs.setValue("library/group", key)
        self._collapsed.clear()
        self.refresh_files()

    # ================================================================== files
    def current_filter(self) -> FileFilter:
        types = {c.frame_type for c in self.chips if c.isChecked()}
        return FileFilter(object_name=self._current_object,
                          object_query=self.obj_search.text().strip() if self._current_object is None else "",
                          types=types, session=self.session_cb.currentData() or None,
                          new_only=self.new_only.isChecked(), text=self.file_search.text().strip())

    def refresh_files(self) -> None:
        if self.ctl.result is None:
            return
        files = filter_files(self.ctl.result.files, self.ctl.statuses, self.current_filter())
        group = self.qs.value("library/group", "object")
        self.model.set_files(files, self.ctl.statuses, group)

    def _after_reset(self) -> None:
        groups = self.model.groups()
        total = sum(len(f) for _, f in groups)
        expand_default = total <= 5000 or len(groups) == 1
        root = QModelIndex()
        for row, (key, _) in enumerate(groups):
            self.tree.setFirstColumnSpanned(row, root, True)
            ix = self.model.index(row, 0)
            self.tree.setExpanded(ix, expand_default and key not in self._collapsed)
        self._update_details()

    def _on_double_click(self, index: QModelIndex) -> None:
        if not index.data(GroupRole):
            self.model.toggle([index.siblingAtColumn(0)])

    def _fit_name_column(self) -> None:
        """The file-name column takes the free width, but never less than ~28 characters
        (narrow windows get a horizontal scrollbar instead of an invisible name column)."""
        others = sum(self.tree.columnWidth(i) for i in range(1, self.model.columnCount())
                     if not self.tree.isColumnHidden(i))
        minimum = self.fontMetrics().averageCharWidth() * 28
        self.tree.setColumnWidth(0, max(minimum, self.tree.viewport().width() - others))

    def eventFilter(self, obj, ev):
        if obj is self.tree.viewport() and ev.type() == QEvent.Type.Resize:
            self._fit_name_column()
        if obj is self.tree and ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Space:
            rows = {ix.siblingAtColumn(0) for ix in self.tree.selectionModel().selectedRows()}
            if not rows and self.tree.currentIndex().isValid():
                rows = {self.tree.currentIndex().siblingAtColumn(0)}
            self.model.toggle(list(rows))
            return True
        return super().eventFilter(obj, ev)

    # ================================================================== details
    def _toggle_details(self) -> None:
        self.details.setVisible(self.btn_details.isChecked())

    def _update_details(self, *_):
        if not hasattr(self, "details"):      # called by the initial model reset during construction
            return
        ix = self.tree.currentIndex().siblingAtColumn(0)
        if ix.isValid() and ix.data(GroupRole):
            self.details.show_group(self.model.group_by, self.model.group_key_at(ix), self.model.group_summary(ix.row()))
        else:
            self.details.show_file(self.model.file_at(ix))


def breakable(text: str) -> str:
    """Allow long paths and file names to wrap after separators (zero-width spaces)."""
    if not text:
        return text
    for ch in ("/", "\\", "_", "-", "."):
        text = text.replace(ch, ch + "​")
    return text


class DetailsPanel(QScrollArea):
    """Read-only metadata for the current frame or group."""

    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.card = Card("Panel")
        self.title = label("", "SectionTitle", wrap=True, selectable=True)
        self.badge = QLabel()
        self.form = QFormLayout()
        self.form.setHorizontalSpacing(14)
        self.form.setVerticalSpacing(7)
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.placeholder = label(t("details.placeholder"), "Muted", wrap=True)
        lay = vbox(self.title, self.badge, self.placeholder, self.form, None, spacing=10, margins=(14, 14, 14, 14))
        self.card.setLayout(lay)
        self.setWidget(self.card)

    def retranslate(self) -> None:
        self.placeholder.setText(t("details.placeholder"))

    def _clear(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)

    def _row(self, key: str, value: str, mono: bool = False, color: Optional[str] = None) -> None:
        k = label(key, "DetailKey")
        v = label(breakable(value) or "—", wrap=True, selectable=True)
        if mono:
            v.setFont(mono_font())
        if color:
            v.setStyleSheet(f"color: {color};")
        self.form.addRow(k, v)

    def _set_badge(self, text: str, color: str) -> None:
        self.badge.setText(text)
        self.badge.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.badge.setVisible(bool(text))

    def show_group(self, group_by: str, key: Optional[str], s: dict) -> None:
        self._clear()
        self.placeholder.hide()
        self.title.setText(key or "")
        self._set_badge(t(f"library.group.{group_by}"), T["muted"])
        self._row(t("details.frames"), str(s["files"]))
        self._row(t("column.size"), fmt_size(s["size"]))
        self._row(t("details.new"), str(s["new"]))
        self._row(t("details.already_local"), str(s["local"]))
        self._row(t("details.selected"), str(s["selected"]))
        if group_by == "object" and self.ctl.dest_root and key:
            from .. import local_state
            try:
                self._row(t("column.saved_to"), str(local_state.dest_dir(key, self.ctl.dest_root)), mono=True)
            except local_state.InvalidName as e:
                self._row(t("column.saved_to"), str(e), color=T["danger"])

    def show_file(self, f) -> None:
        self._clear()
        if f is None:
            self.title.setText("")
            self.badge.hide()
            self.placeholder.show()
            return
        self.placeholder.hide()
        self.title.setText(breakable(f.name))
        ft = f.frame_type or UNKNOWN_TYPE
        self._set_badge(ft, TYPE_COLORS.get(ft, T["muted"]))
        st, detail = self.ctl.statuses.get(f.remote_path, (LocalStatus.NEW, None))
        from .theme import STATUS_COLORS
        self._row(t("column.local"), status_label(st) + (f" – {detail}" if detail else ""),
                  color=STATUS_COLORS.get(st.value))
        self._row(t("column.object"), t("details.source.fits", value=f.object_name) if f.object_source == "FITS"
                  else f.object_name)
        self._row(t("column.session"), f.session_date)
        source = {"FITS": "fits", "prefix": "prefix", "directory": "directory"}.get(f.type_source or "")
        self._row(t("column.type"), t(f"details.source.{source}", value=ft) if source else ft)
        self._row(t("column.filter"), f.filter or "")
        e = exposure_seconds(f)
        self._row(t("column.exposure"), "" if e is None else fmt_seconds(e))
        exact = t("units.b", value=f"{f.size:,}".replace(",", " "))
        self._row(t("column.size"), f"{fmt_size(f.size)}  ({exact})")
        layout = {"A": "a", "B": "b", "A0": "a0"}.get(f.scheme)
        self._row(t("details.layout"), t(f"details.layout.{layout}") if layout else f.scheme)
        self._row(t("details.fits_header"), fits_status_label(f.fits_status))
        for k in ("OBJECT", "IMAGETYP", "DATE-OBS"):
            if f.fits and k in f.fits:
                self._row(f"  {k}", str(f.fits[k]), mono=True)
        if self.ctl.dest_root:
            from .. import local_state
            try:
                self._row(t("column.saved_to"), str(local_state.dest_path(f.object_name, f.name, self.ctl.dest_root)),
                          mono=True)
            except local_state.InvalidName as ex:
                self._row(t("column.saved_to"), str(ex), color=T["danger"])
        self._row(t("details.server_path"), f.remote_path, mono=True)
        if f.notes:
            self._row(t("column.notes"), "\n".join(f.notes), color=T["warning"])
