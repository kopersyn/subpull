"""The translation manager: loads the bundled locale files and answers ``t(key)``.

* English is the source language and the fallback for every missing or broken translation.
* ``t()`` never raises: an unknown key comes back as the key itself, a text whose placeholders
  cannot be filled comes back unformatted.
* Everything is read from local files – no network access, ever.
* Set ``SUBPULL_I18N_DEBUG=1`` to log every key that falls back to English.
"""
from __future__ import annotations

import locale as _locale
import logging
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from . import plurals
from .locale_file import LOCALES_DIR, SOURCE_LANGUAGE, LocaleFileError, is_translated, load, locale_paths

log = logging.getLogger(__name__)

AUTO = "auto"            # settings value: follow the system language


@dataclass(frozen=True)
class LanguageInfo:
    code: str
    name: str             # native name, e.g. "Polski"
    english_name: str     # e.g. "Polish"
    direction: str        # "ltr" or "rtl"
    completion: int       # percent of the English strings that are translated (0–100)

    @property
    def label(self) -> str:
        """Native name, followed by the English name when that adds information."""
        if self.english_name and self.english_name != self.name:
            return f"{self.name} – {self.english_name}"
        return self.name


class _Catalog:
    def __init__(self, directory: Path):
        self.directory = directory
        self.texts: dict[str, dict] = {SOURCE_LANGUAGE: {}}
        self.infos: dict[str, LanguageInfo] = {}
        files = {}
        for path in locale_paths(directory):
            try:
                lf = load(path)
            except LocaleFileError as e:
                # A broken community file must never stop the application.
                log.warning("Translation file %s ignored: %s", path.name, e)
                continue
            code = path.stem
            self.texts[code] = {k: v for k, v in lf.translations.items() if is_translated(v)}
            files[code] = lf
        source = self.texts[SOURCE_LANGUAGE]
        if not source:
            log.error("English translation file is missing or broken – texts are shown as keys")
        for code, lf in files.items():
            done = sum(1 for k in source if k in self.texts[code])
            self.infos[code] = LanguageInfo(
                code, str(lf.meta.get("name") or code), str(lf.meta.get("english_name") or code), lf.direction,
                100 if code == SOURCE_LANGUAGE or not source else done * 100 // len(source))
        # English is always offered, even if its file is unusable.
        self.infos.setdefault(SOURCE_LANGUAGE, LanguageInfo(SOURCE_LANGUAGE, "English", "English", "ltr", 100))


_lock = threading.Lock()
_catalog: Optional[_Catalog] = None
_language = SOURCE_LANGUAGE
_reported: set[tuple[str, str]] = set()


def _cat() -> _Catalog:
    global _catalog
    if _catalog is None:
        with _lock:
            if _catalog is None:
                _catalog = _Catalog(LOCALES_DIR)
    return _catalog


def reload(directory: Optional[Path] = None) -> None:
    """Read the locale files again (``directory`` overrides the bundled one – used by tests)."""
    global _catalog, _language
    with _lock:
        _catalog = _Catalog(Path(directory) if directory else LOCALES_DIR)
        _reported.clear()
        if _language not in _catalog.infos:
            _language = SOURCE_LANGUAGE


# --------------------------------------------------------------------------- languages
def available_languages() -> list[LanguageInfo]:
    """Bundled languages: English first, then by native name."""
    infos = _cat().infos.values()
    return sorted(infos, key=lambda i: (i.code != SOURCE_LANGUAGE, i.name.casefold()))


def match_language(candidates: Iterable[str]) -> str:
    """The best bundled language for the given preferences (``pl_PL.UTF-8``, ``pt-BR``…), else English."""
    codes = {c.lower(): c for c in _cat().infos}
    for cand in candidates:
        tag = str(cand or "").split(".")[0].split("@")[0].replace("_", "-").strip().lower()
        while tag:
            if tag in codes:
                return codes[tag]
            tag = tag.rpartition("-")[0]         # "pt-br" → "pt"
    return SOURCE_LANGUAGE


def system_languages() -> list[str]:
    """The user's preferred languages as reported by the operating system (best effort, no Qt needed)."""
    found: list[str] = []
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        found += [v for v in os.environ.get(var, "").split(":") if v and v not in ("C", "POSIX")]
    if sys.platform == "win32":
        try:
            import ctypes
            found.append(_locale.windows_locale[ctypes.windll.kernel32.GetUserDefaultUILanguage()])
        except Exception:
            pass
    try:
        found.append(_locale.getlocale()[0] or "")
    except (ValueError, TypeError):
        pass
    return [f for f in found if f]


def set_language(code: str = AUTO, system: Optional[Iterable[str]] = None) -> str:
    """Switch the active language. ``"auto"`` (or an unknown code) follows the system language,
    falling back to English. Returns the code that is now active."""
    global _language
    cat = _cat()
    if code and code != AUTO and code in cat.infos:
        resolved = code
    else:
        if code and code != AUTO:
            log.warning("Language '%s' is not available – using the system language", code)
        resolved = match_language(system if system is not None else system_languages())
    _language = resolved
    return resolved


def language() -> str:
    return _language


def direction() -> str:
    info = _cat().infos.get(_language)
    return info.direction if info else "ltr"


# --------------------------------------------------------------------------- lookup
def _debug() -> bool:
    return bool(os.environ.get("SUBPULL_I18N_DEBUG"))


def _report(lang: str, key: str, what: str, level: int) -> None:
    if (lang, key) not in _reported:
        _reported.add((lang, key))
        log.log(level, "i18n [%s] %s: %s", lang, what, key)


def _render(lang: str, value, params: dict) -> str:
    if isinstance(value, dict):
        value = value.get(plurals.category(lang, params.get("count"))) or value.get("other") or ""
    if not params:
        return value
    return value.format(**params)


def t(key: str, **params) -> str:
    """Text for ``key`` in the active language, with ``{placeholders}`` filled from ``params``."""
    cat = _cat()
    lang = _language
    order = (lang, SOURCE_LANGUAGE) if lang != SOURCE_LANGUAGE else (SOURCE_LANGUAGE,)
    for code in order:
        value = cat.texts.get(code, {}).get(key)
        if value is None:
            if code != SOURCE_LANGUAGE and _debug():
                _report(code, key, "missing translation, using English", logging.WARNING)
            continue
        try:
            return _render(code, value, params)
        except (KeyError, IndexError, ValueError, AttributeError, TypeError):
            _report(code, key, "placeholders do not match", logging.WARNING)
            if code == SOURCE_LANGUAGE:
                return value if isinstance(value, str) else str(value.get("other", key))
    if key not in cat.texts.get(SOURCE_LANGUAGE, {}):
        _report(SOURCE_LANGUAGE, key, "unknown translation key", logging.WARNING)
    return key


def number(value: float, spec: str = "") -> str:
    """Format a number with the active language's decimal separator: ``number(1.5, ".1f")`` → ``1,5`` in Polish."""
    text = format(value, spec)
    sep = t("format.decimal_separator")
    return text.replace(".", sep) if sep != "." and len(sep) == 1 else text
