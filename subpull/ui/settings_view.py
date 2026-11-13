"""Settings page, organized in sections: connection, library, scanning, transfers, data."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QLineEdit, QScrollArea, QSpinBox, QWidget)

from .. import __version__, config
from .controller import Controller
from .widgets import Card, StatusDot, button, hbox, label, vbox
from .theme import T


def _section(title: str, subtitle: str, form: QFormLayout, *extra) -> Card:
    card = Card()
    form.setHorizontalSpacing(18)
    form.setVerticalSpacing(10)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    card.setLayout(vbox(label(title, "SectionTitle"), label(subtitle, "Muted", wrap=True), 4, form, *extra,
                        spacing=6, margins=(18, 16, 18, 16)))
    return card


def _ro(text: str = "") -> QLineEdit:
    e = QLineEdit(text)
    e.setReadOnly(True)
    return e


class SettingsPage(QScrollArea):
    def __init__(self, ctl: Controller, choose_destination, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.setWidgetResizable(True)
        self.ctl = ctl
        body = QWidget()
        body.setObjectName("Page")

        # ---- connection -------------------------------------------------------------------
        self.site = QComboBox()
        self.site.setMinimumContentsLength(18)
        self.site.activated.connect(lambda _: self.ctl.select_site(self.site.currentText()))
        self.host, self.user, self.auth = _ro(), _ro(), _ro()
        self.dot = StatusDot()
        self.conn_text = label("", "Muted", wrap=True)
        f1 = QFormLayout()
        f1.addRow("FileZilla site", hbox(self.site, button("Reload sites", "retry", slot=self.ctl.reload_sites), None))
        f1.addRow("Server", self.host)
        f1.addRow("User", self.user)
        f1.addRow("Authentication", self.auth)
        f1.addRow("Status", hbox(self.dot, self.conn_text, None, button("Test connection", "server",
                                                                       slot=self.ctl.test_connection)))
        conn = _section("Connection",
                        "SubPull reuses the sites saved in FileZilla's Site Manager (read-only) and the host keys "
                        f"FileZilla already trusts. Passwords and keys are never shown.  Source: {config.SITEMANAGER_XML}", f1)

        # ---- library ------------------------------------------------------------------------
        self.dest = _ro()
        self.dest.setPlaceholderText("Not set")
        self.btn_open_dest = button("Open", "open", slot=self._open_dest)
        f2 = QFormLayout()
        f2.addRow("Destination", hbox(self.dest, button("Change…", "folder", slot=choose_destination), self.btn_open_dest))
        f2.addRow("Layout", label("<destination> / <OBJECT> / subs / <original file name>", "Muted"))
        lib = _section("Library", "Every frame type goes straight into the object's subs folder. Original names are "
                                  "kept, existing files are never overwritten without your confirmation.", f2)

        # ---- scanning -----------------------------------------------------------------------
        self.source = QLineEdit()
        self.source.editingFinished.connect(lambda: self.ctl.update_settings(source_dir=self.source.text().strip() or "/"))
        self.read_fits = QCheckBox("Read every FITS header while scanning")
        self.read_fits.setToolTip("Verifies object, frame type and filter against the header. Headers of files without "
                                  "an object folder are always read.")
        self.read_fits.toggled.connect(lambda on: self.ctl.update_settings(read_fits=on))
        self.placeholders = QLineEdit()
        self.placeholders.setToolTip("Comma-separated OBJECT values that mean 'no object' (case-insensitive)")
        self.placeholders.editingFinished.connect(self._save_placeholders)
        f3 = QFormLayout()
        f3.addRow("Remote folder", self.source)
        f3.addRow("FITS headers", self.read_fits)
        f3.addRow("Unknown OBJECT values", self.placeholders)
        scan = _section("Scanning", "Scans run only when you press Scan. Session folders (YYYY-MM-DD) are searched up "
                                    "to four levels below the remote folder.", f3)

        # ---- transfers ----------------------------------------------------------------------
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 8)
        self.parallel.valueChanged.connect(lambda v: self.ctl.update_settings(parallel_transfers=v))
        f4 = QFormLayout()
        f4.addRow("Parallel transfers", hbox(self.parallel, label("applies to the next downloads", "Muted"), None))
        f4.addRow("Verification", label("Size check after every file; server SHA-256 when the server supports the "
                                        "SFTP check-file extension.", "Muted", wrap=True))
        f4.addRow("Interrupted files", label(f"Kept as *{config.PART_SUFFIX} and resumed on the next download.",
                                             "Muted", wrap=True))
        transfers = _section("Transfers", "Downloads start only after you confirm them.", f4)

        # ---- data -------------------------------------------------------------------------
        f5 = QFormLayout()
        f5.addRow("Program data", hbox(_ro(str(config.APP_DIR)), button("Open", "open", slot=lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(config.APP_DIR))))))
        f5.addRow("Version", label(f"SubPull {__version__} · MIT license", "Muted"))
        data = _section("Data", "History, settings and the log live here – never inside your frame folders.", f5)

        column = QWidget()
        column.setMaximumWidth(self.fontMetrics().averageCharWidth() * 125)
        column.setLayout(vbox(label("Settings", "PageTitle"), conn, lib, scan, transfers, data, None, spacing=14))
        body.setLayout(hbox(column, None, margins=(20, 16, 20, 16)))
        self.setWidget(body)

        ctl.profileChanged.connect(self.refresh)
        ctl.connectionState.connect(self._on_conn)
        ctl.destinationChanged.connect(self.refresh)
        ctl.settingsChanged.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        s = self.ctl.settings
        self.site.blockSignals(True)
        self.site.clear()
        self.site.addItems(self.ctl.sites)
        self.site.setCurrentText(s.profile_name)
        self.site.blockSignals(False)
        p = self.ctl.profile
        self.host.setText(f"{p.host}:{p.port}  ({p.protocol.upper()})" if p else "")
        self.user.setText(p.user if p else "")
        self.auth.setText(p.auth_label if p else "")
        self.dest.setText(s.dest_root or "")
        self.dest.setCursorPosition(0)
        self.btn_open_dest.setEnabled(bool(s.dest_root))
        for w, v in ((self.source, s.source_dir), (self.placeholders, ", ".join(x for x in s.placeholder_objects if x))):
            if not w.hasFocus():
                w.setText(v)
        self.read_fits.blockSignals(True)
        self.read_fits.setChecked(s.read_fits)
        self.read_fits.blockSignals(False)
        self.parallel.blockSignals(True)
        self.parallel.setValue(int(s.parallel_transfers))
        self.parallel.blockSignals(False)
        self._on_conn(*self.ctl.conn_state)

    def _on_conn(self, state: str, text: str) -> None:
        colors = dict(idle=T["muted"], busy=T["warning"], ok=T["success"], error=T["danger"])
        self.dot.set_color(colors.get(state, T["muted"]))
        self.conn_text.setText(text or {"idle": "Not connected – SubPull connects only when you scan or test.",
                                        "busy": "Connecting…", "ok": "Connected", "error": "Error"}[state])

    def _save_placeholders(self) -> None:
        values = [""] + [v.strip() for v in self.placeholders.text().split(",") if v.strip()]
        self.ctl.update_settings(placeholder_objects=values)

    def _open_dest(self) -> None:
        if self.ctl.dest_root:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctl.dest_root)))
