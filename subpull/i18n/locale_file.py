"""Reading a locale file (``<code>.json``) – shared by the application, the validator and the statistics.

File format::

    {
        "_meta": {"code": "pl", "name": "Polski", "english_name": "Polish", "direction": "ltr", "maintainers": []},
        "translations": {"actions.download": "Pobierz", …}
    }

A translation is a string, or an object of plural forms (see ``plurals.py``). An empty string
means "not translated yet"; the application then shows the English text.
"""
from __future__ import annotations

import json
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

LOCALES_DIR = Path(__file__).parent / "locales"
SOURCE_LANGUAGE = "en"
DIRECTIONS = ("ltr", "rtl")
# BCP 47: language, optional script and region – "pl", "pt-BR", "zh-Hans", "sr-Latn-RS".
CODE_RE = re.compile(r"^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$")

Translation = Union[str, dict]


class LocaleFileError(Exception):
    """The file cannot be used at all (unreadable, not JSON, wrong overall structure)."""


@dataclass
class LocaleFile:
    path: Path
    meta: dict = field(default_factory=dict)
    translations: dict[str, Translation] = field(default_factory=dict)
    duplicates: list[str] = field(default_factory=list)      # keys that appear more than once in the file

    @property
    def code(self) -> str:
        return str(self.meta.get("code") or self.path.stem)

    @property
    def direction(self) -> str:
        d = self.meta.get("direction")
        return d if d in DIRECTIONS else "ltr"


def locale_paths(directory: Path = LOCALES_DIR) -> list[Path]:
    """All locale files, English first."""
    paths = sorted(directory.glob("*.json"), key=lambda p: (p.stem != SOURCE_LANGUAGE, p.stem.lower()))
    return paths


def load(path: Path) -> LocaleFile:
    """Parse one locale file. Raises :class:`LocaleFileError` when it is unusable."""
    duplicates: list[str] = []

    def pairs(items):
        seen = {}
        for k, v in items:
            if k in seen:
                duplicates.append(k)
            seen[k] = v
        return seen

    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise LocaleFileError(f"cannot read file: {e}") from e
    try:
        data = json.loads(text, object_pairs_hook=pairs)
    except ValueError as e:
        raise LocaleFileError(f"invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise LocaleFileError("the file must contain a JSON object with \"_meta\" and \"translations\"")
    meta, translations = data.get("_meta"), data.get("translations")
    if not isinstance(meta, dict):
        raise LocaleFileError("missing \"_meta\" object")
    if not isinstance(translations, dict):
        raise LocaleFileError("missing \"translations\" object")
    return LocaleFile(Path(path), meta, translations, duplicates)


def is_translated(value) -> bool:
    """True when ``value`` holds usable text (a non-empty string, or plural forms with a non-empty ``other``)."""
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        other = value.get("other")
        return isinstance(other, str) and bool(other.strip())
    return False


def placeholders(text: str) -> set[str]:
    """Names of the ``{placeholders}`` in ``text``. Raises ``ValueError`` for unbalanced braces."""
    names = set()
    for _, name, _, _ in string.Formatter().parse(text):
        if name is not None:
            names.add(re.split(r"[.\[]", name, maxsplit=1)[0])
    return names
