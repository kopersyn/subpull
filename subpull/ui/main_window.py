"""Main window: collapsible sidebar, top toolbar and the page stack."""
from __future__ import annotations

from PySide6.QtCore import QSettings, QSize, Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QSizePolicy, QStackedWidget, QToolButton, QWidget)

from .. import config
from ..i18n import t
from . import icons
from .controller import Controller
from .dialogs import ConfirmDownloadDialog, UnrecognizedDialog, message, show_error
from .history_view import HistoryPage
from .library import LibraryPage
from .logs_view import LogsPage
from .models import LogModel
from .queue_view import QueuePage
from .settings_view import SettingsPage
from .theme import T
from .widgets import StatusDot, button, hbox, label, vbox

CONN_COLORS = dict(idle=T["muted"], busy=T["warning"], ok=T["success"], error=T["danger"])
# (page id, translation key of its name, icon)
PAGES = [("library", "nav.library", "library"), ("queue", "nav.queue", "queue"), ("history", "nav.history", "history"),
         ("logs", "nav.logs", "logs"), ("settings", "nav.settings", "settings")]


class Sidebar(QFrame):
    def __init__(self, on_select, qs: QSettings, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.qs = qs
        fm = self.fontMetrics()
        self.logo = QToolButton()
        self.logo.setIcon(icons.logo_icon())
        self.logo.setIconSize(QSize(int(fm.height() * 1.7), int(fm.height() * 1.7)))
        self.logo.setEnabled(True)
        self.logo.setToolTip(config.APP_NAME)
        self.brand = label(config.APP_NAME, "BrandName")
        brand_row = hbox(self.logo, self.brand, None, spacing=4)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QToolButton] = {}
        nav = vbox(spacing=2)
        for key, _, ic in PAGES:
            b = QToolButton()
            b.setIcon(icons.icon(ic, T["muted"], T["text"]))
            b.setIconSize(QSize(int(fm.height() * 1.15), int(fm.height() * 1.15)))
            b.setCheckable(True)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: on_select(k))
            self.group.addButton(b)
            self.buttons[key] = b
            nav.addWidget(b)
        self._badges: dict[str, str] = {}
        self._conn: tuple[str, str] = ("idle", "")

        self.dot = StatusDot()
        self.conn_label = label("", "SidebarFooter")
        self.conn_label.setWordWrap(True)
        self.collapse = QToolButton()
        self.collapse.setObjectName("Ghost")
        self.collapse.setIcon(icons.icon("sidebar", T["muted"]))
        self.collapse.clicked.connect(lambda: self.set_collapsed(not self.collapsed))
        self.footer = hbox(self.dot, self.conn_label, None, spacing=8)
        self.setLayout(vbox(brand_row, 14, nav, None, self.footer, 6, hbox(self.collapse, None),
                            spacing=4, margins=(10, 12, 10, 10)))
        self.collapsed = qs.value("sidebar/collapsed", False, type=bool)
        self.retranslate()

    def retranslate(self) -> None:
        for i, (key, text_key, _) in enumerate(PAGES):
            self.buttons[key].setToolTip(t("nav.tooltip", page=t(text_key), shortcut=f"Ctrl+{i + 1}"))
            self.set_badge(key, self._badges.get(key, ""))
        self.set_collapsed(self.collapsed)
        self.set_connection(*self._conn)

    def _expanded_width(self) -> int:
        """Wide enough for the longest page name in the current language (plus icon, padding and a badge)."""
        fm = self.fontMetrics()
        longest = max(fm.horizontalAdvance(t(text_key)) for _, text_key, _ in PAGES)
        return max(fm.averageCharWidth() * 27, longest + int(fm.height() * 1.15) + fm.averageCharWidth() * 9)

    def set_collapsed(self, collapsed: bool) -> None:
        self.collapsed = collapsed
        self.qs.setValue("sidebar/collapsed", collapsed)
        style = Qt.ToolButtonStyle.ToolButtonIconOnly if collapsed else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        for b in self.buttons.values():
            b.setToolButtonStyle(style)
        self.brand.setVisible(not collapsed)
        self.conn_label.setVisible(not collapsed)
        self.collapse.setToolTip(t("nav.expand" if collapsed else "nav.collapse"))
        self.setFixedWidth(int(self.fontMetrics().height() * 3.6) if collapsed else self._expanded_width())

    def set_active(self, key: str) -> None:
        self.buttons[key].setChecked(True)

    def set_badge(self, key: str, text: str) -> None:
        self._badges[key] = text
        name = t(next(text_key for k, text_key, _ in PAGES if k == key))
        self.buttons[key].setText(f"{name}   {text}" if text else name)

    def set_connection(self, state: str, text: str) -> None:
        self._conn = (state, text)
        self.dot.set_color(CONN_COLORS.get(state, T["muted"]))
        text = text or (t(f"connection.{state}") if state in CONN_COLORS else "")
        self.dot.setToolTip(text)
        self.conn_label.setText(text)


class TopBar(QFrame):
    def __init__(self, win: "MainWindow", parent=None):
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.win = win
        ctl = win.ctl
        fm = self.fontMetrics()

        self.site = QToolButton()
        self.site.setObjectName("PillButton")
        self.site.setIcon(icons.icon("server", T["text2"]))
        self.site.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.site.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.site.setCursor(Qt.CursorShape.PointingHandCursor)
        self.site_menu = QMenu(self)
        self.site.setMenu(self.site_menu)
        self.site_menu.aboutToShow.connect(self._fill_site_menu)

        self.dot = StatusDot()
        self.conn = label("", "Muted")
        self.conn.setMinimumWidth(fm.averageCharWidth() * 16)
        self.conn.setMaximumWidth(fm.averageCharWidth() * 46)

        self.source = QLineEdit()
        self.source.addAction(icons.icon("folder", T["muted"]), QLineEdit.ActionPosition.LeadingPosition)
        self.source.setFixedWidth(fm.averageCharWidth() * 24)
        self.source.returnPressed.connect(win.scan)
        self.source.editingFinished.connect(lambda: ctl.update_settings(source_dir=self.source.text().strip() or "/"))

        self.scan_btn = button("", "scan", "Primary", slot=win.scan_or_cancel)
        self._scanning = False
        self.scan_info = label("", "Muted")

        self.dest = QToolButton()
        self.dest.setObjectName("PillButton")
        self.dest.setIcon(icons.icon("folder", T["text2"]))
        self.dest.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.dest.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.dest.setCursor(Qt.CursorShape.PointingHandCursor)
        dm = QMenu(self)
        self.change_dest = dm.addAction(icons.icon("folder"), "", win.choose_destination)
        self.open_dest = dm.addAction(icons.icon("open"), "", win.open_destination)
        self.dest.setMenu(dm)

        self.setLayout(hbox(self.site, self.dot, self.conn, None, self.scan_info, self.source, self.scan_btn, 12,
                            self.dest, spacing=8, margins=(16, 10, 16, 10)))

        ctl.profileChanged.connect(self.refresh)
        ctl.destinationChanged.connect(self.refresh)
        ctl.settingsChanged.connect(self.refresh)
        ctl.connectionState.connect(self.set_connection)
        ctl.scanRunning.connect(self.set_scanning)
        ctl.scanProgress.connect(self.scan_info.setText)
        self.retranslate()

    def retranslate(self) -> None:
        self.source.setPlaceholderText(t("topbar.source.placeholder"))
        self.source.setToolTip(t("topbar.source.tooltip"))
        self.scan_btn.setToolTip(t("topbar.scan.tooltip"))
        self.scan_btn.setText(t("actions.cancel" if self._scanning else "actions.scan"))
        self.change_dest.setText(t("topbar.destination.change"))
        self.open_dest.setText(t("topbar.destination.open"))
        self.refresh()

    def _fill_site_menu(self) -> None:
        m = self.site_menu
        m.clear()
        ctl = self.win.ctl
        grp = QActionGroup(m)
        for name in ctl.sites:
            a = m.addAction(name)
            a.setCheckable(True)
            a.setChecked(name == ctl.settings.profile_name)
            a.triggered.connect(lambda _=False, n=name: ctl.select_site(n))
            grp.addAction(a)
        if not ctl.sites:
            m.addAction(t("topbar.site.no_sites")).setEnabled(False)
        m.addSeparator()
        m.addAction(icons.icon("retry"), t("topbar.site.reload"), ctl.reload_sites)
        m.addAction(icons.icon("server"), t("actions.test_connection"), ctl.test_connection)
        m.addAction(icons.icon("settings"), t("topbar.site.settings"), lambda: self.win.show_page("settings"))

    def refresh(self) -> None:
        ctl = self.win.ctl
        fm = self.fontMetrics()
        if ctl.conn:
            text = f"{ctl.settings.profile_name}  ·  {ctl.conn.label}"
            self.site.setToolTip("\n".join((t("topbar.site.tooltip", site=ctl.settings.profile_name), ctl.conn.label,
                                            ctl.profile.auth_label)))
        else:
            text = t("topbar.site.none")
            self.site.setToolTip(ctl.profile_error or "")
        self.site.setText(fm.elidedText(text, Qt.TextElideMode.ElideMiddle, fm.averageCharWidth() * 40))
        if not self.source.hasFocus():
            self.source.setText(ctl.settings.source_dir)
        d = ctl.dest_root
        self.dest.setText(fm.elidedText(str(d), Qt.TextElideMode.ElideMiddle, fm.averageCharWidth() * 34)
                          if d else t("topbar.destination.choose"))
        self.dest.setToolTip(t("topbar.destination.tooltip", folder=d) if d else t("topbar.destination.none"))
        self.open_dest.setEnabled(bool(d))
        self.set_connection(*ctl.conn_state)

    def set_connection(self, state: str, text: str) -> None:
        self.dot.set_color(CONN_COLORS.get(state, T["muted"]))
        full = text or t(f"connection.{state}")
        self.conn.setText(self.fontMetrics().elidedText(full, Qt.TextElideMode.ElideRight, self.conn.maximumWidth()))
        self.conn.setToolTip(full)

    def set_scanning(self, running: bool) -> None:
        self._scanning = running
        self.scan_btn.setText(t("actions.cancel" if running else "actions.scan"))
        self.scan_btn.setObjectName("" if running else "Primary")
        self.scan_btn.setIcon(icons.icon("stop" if running else "scan", T["text2"] if running else "#ffffff"))
        self.scan_btn.style().unpolish(self.scan_btn)
        self.scan_btn.style().polish(self.scan_btn)
        self.source.setEnabled(not running)
        self.site.setEnabled(not running)
        if not running:
            self.scan_info.setText("")


class MainWindow(QMainWindow):
    def __init__(self, ctl: Controller, log_model: LogModel, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.qs = QSettings(str(config.APP_DIR / "ui.ini"), QSettings.Format.IniFormat)
        self.setWindowTitle(config.APP_NAME)
        self.setWindowIcon(icons.logo_icon())
        fm = self.fontMetrics()
        self.setMinimumSize(fm.averageCharWidth() * 150, fm.height() * 38)

        self.sidebar = Sidebar(self.show_page, self.qs)
        self.topbar = TopBar(self)
        self.library = LibraryPage(ctl, self.qs)
        self.queue = QueuePage(ctl, self.qs)
        self.history = HistoryPage(ctl, self.qs)
        self.logs = LogsPage(log_model)
        self.settings = SettingsPage(ctl, self.choose_destination)
        self.pages = {"library": self.library, "queue": self.queue, "history": self.history,
                      "logs": self.logs, "settings": self.settings}
        self.stack = QStackedWidget()
        for p in self.pages.values():
            self.stack.addWidget(p)

        right = QWidget()
        right.setLayout(vbox(self.topbar, self.stack, spacing=0))
        central = QWidget()
        central.setLayout(hbox(self.sidebar, right, spacing=0))
        self.setCentralWidget(central)

        # ---- wiring -----------------------------------------------------------------------
        self.library.downloadRequested.connect(self.confirm_download)
        self.library.unrecognizedRequested.connect(self.show_unrecognized)
        self.library.chooseDestination.connect(self.choose_destination)
        self.library.scanRequested.connect(self.scan)
        ctl.errorRaised.connect(lambda e, retry: show_error(self, e, retry))
        ctl.languageChanged.connect(self.retranslate)
        ctl.connectionState.connect(self.sidebar.set_connection)
        ctl.jobUpdated.connect(lambda _: self._update_badges())
        ctl.queueIdle.connect(self._update_badges)
        log_model.rowsInserted.connect(lambda *_: self._update_badges())
        self._log_model = log_model

        for i, (key, _, _) in enumerate(PAGES):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda k=key: self.show_page(k))
        QShortcut(QKeySequence("F5"), self, activated=self.scan)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: (self.show_page("library"), self.library.file_search.setFocus()))

        self.show_page(self.qs.value("window/page", "library"))
        if self.qs.contains("window/geometry"):
            self.restoreGeometry(self.qs.value("window/geometry"))
        else:
            self.resize(fm.averageCharWidth() * 210, fm.height() * 52)
        self.sidebar.set_connection(*ctl.conn_state)

    def retranslate(self) -> None:
        """Re-apply every text in the selected language to the existing widgets."""
        self.sidebar.retranslate()
        self.topbar.retranslate()
        for page in self.pages.values():
            page.retranslate()
        self._update_badges()

    # ------------------------------------------------------------------ navigation
    def show_page(self, key: str) -> None:
        if key not in self.pages:
            key = "library"
        self.stack.setCurrentWidget(self.pages[key])
        self.sidebar.set_active(key)
        self.qs.setValue("window/page", key)

    def _update_badges(self) -> None:
        s = self.ctl.queue_stats()
        self.sidebar.set_badge("queue", f"{s.active + s.queued}" if s.running else "")
        err, _ = self._log_model.counts()
        self.sidebar.set_badge("logs", f"{err}" if err else "")

    # ------------------------------------------------------------------ actions
    def scan(self) -> None:
        if not self.ctl.scanning:
            self.topbar.source.clearFocus()
            self.ctl.start_scan(self.topbar.source.text())

    def scan_or_cancel(self) -> None:
        if self.ctl.scanning:
            self.ctl.cancel_scan()
        else:
            self.scan()

    def choose_destination(self) -> bool:
        start = str(self.ctl.dest_root) if self.ctl.dest_root and self.ctl.dest_root.exists() else str(config.Path.home())
        d = QFileDialog.getExistingDirectory(self, t("dialog.destination.title"), start)
        if not d:
            return False
        self.ctl.set_destination(d)
        return True

    def open_destination(self) -> None:
        if self.ctl.dest_root:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctl.dest_root)))

    def show_unrecognized(self) -> None:
        if self.ctl.result:
            UnrecognizedDialog(self.ctl.result, self).exec()

    def confirm_download(self) -> None:
        if not self.ctl.selected:
            return
        if self.ctl.dest_root is None:
            message(self, QMessageBox.Icon.Information, t("dialog.destination_required.title"),
                    t("dialog.destination_required.text"))
            if not self.choose_destination():
                return
        dlg = ConfirmDownloadDialog(self.ctl, self)
        if dlg.exec() == ConfirmDownloadDialog.DialogCode.Accepted and dlg.plan.todo:
            if self.ctl.start_download(dlg.plan.todo, dlg.overwrite_states()):
                self.show_page("queue")

    # ------------------------------------------------------------------ shutdown
    def closeEvent(self, ev):
        if self.ctl.downloading:
            if message(self, QMessageBox.Icon.Question, t("dialog.quit.title"), t("dialog.quit.text"),
                       ("yes", "no"), default="no") != "yes":
                ev.ignore()
                return
        self.qs.setValue("window/geometry", self.saveGeometry())
        for p in (self.library, self.queue, self.history):
            p.save_state()
        self.qs.sync()
        self.ctl.shutdown()
        ev.accept()
