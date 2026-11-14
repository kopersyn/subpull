"""Localization for SubPull.

Translations are plain JSON files in ``subpull/i18n/locales`` – one file per language,
maintained by the community (see ``translations/README.md``). This package has no GUI
dependency, so the core modules and the command-line tools can use it too.

    from subpull.i18n import t
    t("actions.download")
    t("count.files", count=5)
"""
from .manager import (AUTO, SOURCE_LANGUAGE, LanguageInfo, available_languages, direction, language, match_language,
                      number, reload, set_language, system_languages, t)

__all__ = ["AUTO", "SOURCE_LANGUAGE", "LanguageInfo", "available_languages", "direction", "language",
           "match_language", "number", "reload", "set_language", "system_languages", "t"]
