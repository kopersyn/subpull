"""Settings page, organized in sections: language, connection, library, scanning, transfers, data."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QLabel, QLineEdit, QScrollArea, QSpinBox, QWidget)

from .. import __version__, config, i18n
from ..i18n import t
from . import language
from .controller import Controller
from .language import TRANSLATION_GUIDE_URL
from .widgets import Card, StatusDot, Texts, hbox, label, vbox
from .theme import T


def _section(title: QLabel, subtitle: QLabel, form: QFormLayout, *extra) -> Card:
    card = Card()
    form.setHorizontalSpacing(18)
    form.setVerticalSpacing(10)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    card.setLayout(vbox(title, subtitle, 4, form, *extra,
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
        self.tx = tx = Texts()
        body = QWidget()
        body.setObjectName("Page")

        def section(name: str, form: QFormLayout, **params) -> Card:
            return _section(tx.label(f"settings.{name}.title", "SectionTitle"),
                            tx.label(f"settings.{name}.subtitle", "Muted", wrap=True, **params), form)

        # ---- language ---------------------------------------------------------------------
        self.language = QComboBox()
        self.language.setMinimumContentsLength(28)
        self.language.activated.connect(lambda _: self.ctl.set_language(self.language.currentData()))
        f0 = QFormLayout()
        f0.addRow(tx.label("settings.language.label"),
                  hbox(self.language, tx.button("settings.language.help", "open", tooltip="settings.language.help.tooltip",
                                                slot=lambda: QDesktopServices.openUrl(QUrl(TRANSLATION_GUIDE_URL))), None))
        lang = section("language", f0)

        # ---- connection -------------------------------------------------------------------
        self.site = QComboBox()
        self.site.setMinimumContentsLength(18)
        self.site.activated.connect(lambda _: self.ctl.select_site(self.site.currentText()))
        self.host, self.user, self.auth = _ro(), _ro(), _ro()
        self.dot = StatusDot()
        self.conn_text = label("", "Muted", wrap=True)
        f1 = QFormLayout()
        f1.addRow(tx.label("settings.connection.site"),
                  hbox(self.site, tx.button("settings.connection.reload", "retry", slot=self.ctl.reload_sites), None))
        f1.addRow(tx.label("settings.connection.server"), self.host)
        f1.addRow(tx.label("settings.connection.user"), self.user)
        f1.addRow(tx.label("settings.connection.auth"), self.auth)
        f1.addRow(tx.label("settings.connection.status"),
                  hbox(self.dot, self.conn_text, None,
                       tx.button("actions.test_connection", "server", slot=self.ctl.test_connection)))
        conn = section("connection", f1, path=config.SITEMANAGER_XML)

        # ---- library ------------------------------------------------------------------------
        self.dest = _ro()
        tx.bind(self.dest.setPlaceholderText, "settings.library.not_set")
        self.btn_open_dest = tx.button("actions.open", "open", slot=self._open_dest)
        f2 = QFormLayout()
        f2.addRow(tx.label("settings.library.destination"),
                  hbox(self.dest, tx.button("settings.library.change", "folder", slot=choose_destination),
                       self.btn_open_dest))
        f2.addRow(tx.label("settings.library.layout"), tx.label("settings.library.layout_value", "Muted"))
        lib = section("library", f2)

        # ---- scanning -----------------------------------------------------------------------
        self.source = QLineEdit()
        self.source.editingFinished.connect(lambda: self.ctl.update_settings(source_dir=self.source.text().strip() or "/"))
        self.read_fits = QCheckBox()
        tx.bind(self.read_fits.setText, "settings.scanning.read_fits")
        tx.bind(self.read_fits.setToolTip, "settings.scanning.read_fits.tooltip")
        self.read_fits.toggled.connect(lambda on: self.ctl.update_settings(read_fits=on))
        self.placeholders = QLineEdit()
        tx.bind(self.placeholders.setToolTip, "settings.scanning.placeholders.tooltip")
        self.placeholders.editingFinished.connect(self._save_placeholders)
        f3 = QFormLayout()
        f3.addRow(tx.label("settings.scanning.remote_folder"), self.source)
        f3.addRow(tx.label("settings.scanning.fits_headers"), self.read_fits)
        f3.addRow(tx.label("settings.scanning.placeholders"), self.placeholders)
        scan = section("scanning", f3)

        # ---- transfers ----------------------------------------------------------------------
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 8)
        self.parallel.valueChanged.connect(lambda v: self.ctl.update_settings(parallel_transfers=v))
        f4 = QFormLayout()
        f4.addRow(tx.label("settings.transfers.parallel"),
                  hbox(self.parallel, tx.label("settings.transfers.parallel.hint", "Muted"), None))
        f4.addRow(tx.label("settings.transfers.verification"),
                  tx.label("settings.transfers.verification.text", "Muted", wrap=True))
        f4.addRow(tx.label("settings.transfers.interrupted"),
                  tx.label("settings.transfers.interrupted.text", "Muted", wrap=True, suffix=config.PART_SUFFIX))
        transfers = section("transfers", f4)

        # ---- data -------------------------------------------------------------------------
        f5 = QFormLayout()
        f5.addRow(tx.label("settings.data.program_data"),
                  hbox(_ro(str(config.APP_DIR)), tx.button("actions.open", "open", slot=lambda: QDesktopServices.openUrl(
                      QUrl.fromLocalFile(str(config.APP_DIR))))))
        f5.addRow(tx.label("settings.data.version"), tx.label("settings.data.version.text", "Muted", version=__version__))
        data = section("data", f5)

        column = QWidget()
        column.setMaximumWidth(self.fontMetrics().averageCharWidth() * 125)
        column.setLayout(vbox(tx.label("nav.settings", "PageTitle"), lang, conn, lib, scan, transfers, data, None,
                              spacing=14))
        body.setLayout(hbox(column, None, margins=(20, 16, 20, 16)))
        self.setWidget(body)

        ctl.profileChanged.connect(self.refresh)
        ctl.connectionState.connect(self._on_conn)
        ctl.destinationChanged.connect(self.refresh)
        ctl.settingsChanged.connect(self.refresh)
        self.refresh()

    def retranslate(self) -> None:
        self.tx.retranslate()
        self.refresh()

    def _fill_languages(self) -> None:
        """Automatic first, then every bundled language by its native name."""
        system = i18n.match_language(language.system_languages())
        infos = {i.code: i for i in i18n.available_languages()}
        self.language.blockSignals(True)
        self.language.clear()
        self.language.addItem(t("settings.language.auto", language=infos[system].name), i18n.AUTO)
        for info in infos.values():
            text = info.label if info.completion >= 100 else t("settings.language.partial", language=info.label,
                                                                percent=info.completion)
            self.language.addItem(text, info.code)
        current = self.ctl.settings.language
        self.language.setCurrentIndex(max(0, self.language.findData(current if current in infos else i18n.AUTO)))
        self.language.blockSignals(False)

    def refresh(self) -> None:
        s = self.ctl.settings
        self._fill_languages()
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
        self.conn_text.setText(text or t({"idle": "settings.connection.idle", "busy": "connection.busy",
                                          "ok": "connection.ok", "error": "settings.connection.error"}[state]))

    def _save_placeholders(self) -> None:
        values = [""] + [v.strip() for v in self.placeholders.text().split(",") if v.strip()]
        self.ctl.update_settings(placeholder_objects=values)

    def _open_dest(self) -> None:
        if self.ctl.dest_root:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctl.dest_root)))
