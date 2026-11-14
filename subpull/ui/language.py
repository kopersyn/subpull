"""Qt side of localization: system language detection, layout direction and Qt's own texts."""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QLibraryInfo, QLocale, Qt, QTranslator
from PySide6.QtGui import QGuiApplication

from .. import i18n

log = logging.getLogger(__name__)

TRANSLATION_GUIDE_URL = "https://github.com/kopersyn/subpull/blob/main/translations/README.md"

_qt_translator: Optional[QTranslator] = None


def system_languages() -> list[str]:
    """The user's preferred interface languages, most preferred first."""
    return list(QLocale.system().uiLanguages()) + i18n.system_languages()


def apply(preference: str) -> str:
    """Activate the language from the settings (a code, or "auto" for the system language) and
    adjust the running application: layout direction and Qt's built-in texts (context menus of
    text fields, the file dialog…). Returns the active language code."""
    global _qt_translator
    code = i18n.set_language(preference, system_languages())
    app = QGuiApplication.instance()
    if app is None:
        return code
    rtl = i18n.direction() == "rtl"
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if rtl else Qt.LayoutDirection.LeftToRight)
    if _qt_translator is not None:
        app.removeTranslator(_qt_translator)
        _qt_translator = None
    if code != i18n.SOURCE_LANGUAGE:
        # Qt ships translations of its own widgets for many languages; use them when present (local files only).
        tr = QTranslator(app)
        folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        if tr.load(QLocale(code.replace("-", "_")), "qtbase", "_", folder):
            app.installTranslator(tr)
            _qt_translator = tr
        else:
            tr.deleteLater()
    return code
